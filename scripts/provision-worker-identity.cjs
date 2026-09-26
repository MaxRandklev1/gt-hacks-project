// Creates a project-specific worker identity; never prints or logs private keys.
const fs = require('node:fs/promises');
const path = require('node:path');
const { authenticated } = require('./firebase-admin-client.cjs');
(async () => {
  const [project, output] = process.argv.slice(2);
  if (!output || !path.isAbsolute(output)) throw new Error('Provide project ID and an absolute credential path outside the repository.');
  const target = path.resolve(output);
  const root = path.resolve(__dirname,'..');
  if (target.startsWith(root + path.sep)) throw new Error('Save worker credentials outside this repository and cloud-synced project folder.');
  const api = await authenticated(project);
  const iam = api.client('https://iam.googleapis.com');
  const email = `thread-gpu-worker@${project}.iam.gserviceaccount.com`;
  try { await iam.get(`/v1/projects/${project}/serviceAccounts/${email}`,{skipLog:{resBody:true}}); }
  catch (error) {
    if (error.status !== 404 && error.context?.response?.statusCode !== 404 && !error.message.includes('404')) throw error;
    await iam.post(`/v1/projects/${project}/serviceAccounts`,{accountId:'thread-gpu-worker',serviceAccount:{displayName:'THREAD local GPU worker'}},{skipLog:{resBody:true}});
  }
  const crm = api.client('https://cloudresourcemanager.googleapis.com');
  const policy = (await crm.post(`/v1/projects/${project}:getIamPolicy`,{}, {skipLog:{resBody:true}})).body;
  policy.bindings ||= [];
  const role = 'roles/datastore.user';
  let binding = policy.bindings.find(b=>b.role===role && !b.condition);
  if (!binding) {binding={role,members:[]};policy.bindings.push(binding);}
  const member = `serviceAccount:${email}`;
  if (!binding.members.includes(member)) {
    binding.members.push(member);
    await crm.post(`/v1/projects/${project}:setIamPolicy`,{policy},{skipLog:{reqBody:true,resBody:true}});
  }
  const bucketName = `${project}.firebasestorage.app`;
  const gcs = api.client('https://storage.googleapis.com');
  const bucketPolicy = (await gcs.get(`/storage/v1/b/${bucketName}/iam`,{skipLog:{resBody:true}})).body;
  bucketPolicy.bindings ||= [];
  let storageChanged = false;
  for (const bucketRole of ['roles/storage.objectViewer','roles/storage.objectCreator']) {
    let storageBinding=bucketPolicy.bindings.find(b=>b.role===bucketRole && !b.condition);
    if(!storageBinding){storageBinding={role:bucketRole,members:[]};bucketPolicy.bindings.push(storageBinding);}
    if(!storageBinding.members.includes(member)) {storageBinding.members.push(member);storageChanged=true;}
  }
  if(storageChanged) {
    await gcs.put(`/storage/v1/b/${bucketName}/iam`,bucketPolicy,{skipLog:{reqBody:true,resBody:true}});
  }
  try { await fs.access(target); console.log('Worker identity configured; existing local key retained.'); return; }
  catch(error) {if(error.code!=='ENOENT')throw error;}
  const key = await iam.post(`/v1/projects/${project}/serviceAccounts/${email}/keys`,{privateKeyType:'TYPE_GOOGLE_CREDENTIALS_FILE',keyAlgorithm:'KEY_ALG_RSA_2048'},{skipLog:{reqBody:true,resBody:true}});
  await fs.mkdir(path.dirname(target),{recursive:true});
  await fs.writeFile(target,Buffer.from(key.body.privateKeyData,'base64'),{flag:'wx',mode:0o600});
  console.log('Worker identity configured. Private credential saved outside the repository.');
})().catch(error=>{console.error(error.message);process.exitCode=1;});
