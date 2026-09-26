const { authenticated } = require('./firebase-admin-client.cjs');
(async () => {
  const api = await authenticated(process.argv[2]);
  for (const name of ['firestore.googleapis.com','identitytoolkit.googleapis.com','firebasestorage.googleapis.com','iam.googleapis.com']) await api.ensure(name);
  console.log('Firebase service APIs are enabled. This does not attach billing or change the pricing plan.');
})().catch(error => { console.error(error.message); process.exitCode = 1; });
