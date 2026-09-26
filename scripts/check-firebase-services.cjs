const { authenticated } = require('./firebase-admin-client.cjs');
(async () => {
  const project = process.argv[2];
  const api = await authenticated(project);
  const result = await api.client('https://serviceusage.googleapis.com').get(`/v1/projects/${project}/services/firestore.googleapis.com`);
  console.log(JSON.stringify({name: result.body.name,state:result.body.state}));
  const billing = await api.client('https://cloudbilling.googleapis.com').get(`/v1/projects/${project}/billingInfo`);
  console.log(JSON.stringify({billingEnabled:billing.body.billingEnabled}));
})().catch(error => { console.error(error.message); process.exitCode = 1; });
