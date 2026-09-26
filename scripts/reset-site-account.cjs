// Operator-only account reset. Uses the signed-in Firebase CLI, never a web credential.
const { authenticated } = require('./firebase-admin-client.cjs');
const fs = require('node:fs/promises');
const path = require('node:path');

(async () => {
  const [project, email, flag] = process.argv.slice(2);
  if (!email || (flag && flag !== '--execute')) throw new Error('Usage: node scripts/reset-site-account.cjs PROJECT EMAIL [--execute]');
  const api = await authenticated(project);
  const quiet = () => ({ skipLog: { reqBody: true, resBody: true } });
  const auth = api.client('https://identitytoolkit.googleapis.com');
  const accounts = (await auth.post(`/v1/projects/${project}/accounts:lookup`, { email: [email] }, quiet())).body.users || [];
  if (accounts.length !== 1 || accounts[0].email.toLowerCase() !== email.toLowerCase()) throw new Error('The exact Google sign-in account was not found.');
  const account = accounts[0], uid = account.localId;
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(uid)) throw new Error('Unsupported account identifier.');
  const firestore = api.client('https://firestore.googleapis.com');
  const base = `projects/${project}/databases/(default)/documents`;
  const root = `${base}/users/${uid}`;
  const documents = [];
  async function descendants(documentName) {
    let pageToken;
    do {
      const listed = (await firestore.post(`/v1/${documentName}:listCollectionIds`, { pageSize: 100, ...(pageToken ? { pageToken } : {}) }, quiet())).body;
      for (const collectionId of listed.collectionIds || []) {
        let nextPage;
        do {
          const response = (await firestore.get(`/v1/${documentName}/${encodeURIComponent(collectionId)}`, { ...quiet(), queryParams: { pageSize: 100, showMissing: 'true', ...(nextPage ? { pageToken: nextPage } : {}) } })).body;
          for (const document of response.documents || []) {
            if (!document.name.startsWith(root + '/')) throw new Error('A document escaped this account.');
            if (documents.length >= 20000) throw new Error('Account is too large for this bounded reset.');
            await descendants(document.name);
            documents.push(document);
          }
          nextPage = response.nextPageToken;
        } while (nextPage);
      }
      pageToken = listed.nextPageToken;
    } while (pageToken);
  }
  await descendants(root);
  const rows = (await firestore.post(`/v1/${base}:runQuery`, { structuredQuery: {
    from: [{ collectionId: 'jobs' }], where: { fieldFilter: { field: { fieldPath: 'uid' }, op: 'EQUAL', value: { stringValue: uid } } }, limit: 20001,
  } }, quiet())).body;
  const jobs = rows.filter(row => row.document).map(row => row.document);
  if (jobs.length > 20000 || jobs.some(job => job.fields?.uid?.stringValue !== uid)) throw new Error('Unexpected job scope.');
  const active = jobs.filter(job => ['queued', 'running'].includes(job.fields.status?.stringValue));
  const gcs = api.client('https://storage.googleapis.com');
  const bucket = `${project}.firebasestorage.app`, prefix = `users/${uid}/`;
  const objects = [];
  let next;
  do {
    const listed = (await gcs.get(`/storage/v1/b/${bucket}/o`, { ...quiet(), queryParams: { prefix, maxResults: 1000, ...(next ? { pageToken: next } : {}) } })).body;
    for (const object of listed.items || []) {
      if (!object.name.startsWith(prefix)) throw new Error('A storage object escaped this account.');
      objects.push(object);
    }
    if (objects.length > 20000) throw new Error('Too many objects for this bounded reset.');
    next = listed.nextPageToken;
  } while (next);
  console.log(JSON.stringify({ exactAccount: email, mode: flag === '--execute' ? 'execute' : 'preview', jobs: jobs.length, activeJobs: active.length, childDocuments: documents.length, storageObjects: objects.length, storageBytes: objects.reduce((sum, object) => sum + Number(object.size), 0) }));
  if (flag !== '--execute') return;
  if (active.length) throw new Error('Wait for this account’s queued/running jobs to finish before resetting.');
  // Refuse to race a live worker; stop the verified worker before this operation.
  const stateFile = path.join(__dirname, '..', '.local', 'worker-process.json');
  try {
    const state = JSON.parse(await fs.readFile(stateFile, 'utf8'));
    let alive = true;
    try { process.kill(state.pid, 0); } catch (error) { if (error.code === 'ESRCH') alive = false; else throw error; }
    if (alive) throw new Error('Stop the local GPU worker before executing this reset.');
  } catch (error) { if (error.code !== 'ENOENT') throw error; }
  for (const object of objects) await gcs.delete(`/storage/v1/b/${bucket}/o/${encodeURIComponent(object.name)}`, { ...quiet(), queryParams: { ifGenerationMatch: object.generation } });
  for (const document of [...documents, ...jobs]) await firestore.delete(`/v1/${document.name}`, { ...quiet(), queryParams: document.updateTime ? { 'currentDocument.updateTime': document.updateTime } : {} });
  // Keep the Google account and replace only its app profile so open clients return to onboarding.
  const stamp = new Date().toISOString();
  await firestore.patch(`/v1/${root}`, { fields: {
    displayName: { stringValue: account.displayName || 'Your profile' }, email: { stringValue: account.email },
    photoURL: { stringValue: account.photoUrl || '' }, createdAt: { timestampValue: stamp }, updatedAt: { timestampValue: stamp },
  } }, quiet());
  await fs.mkdir(path.join(__dirname, '..', '.local'), { recursive: true });
  await fs.writeFile(path.join(__dirname, '..', '.local', 'last-account-reset.json'), JSON.stringify({ uid, jobIds: jobs.map(job => job.name.split('/').pop()), resetAt: stamp }));
  console.log('Account app data cleared; Google sign-in retained and onboarding reset.');
})().catch(error => { console.error(error.message); process.exitCode = 1; });
