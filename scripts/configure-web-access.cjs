const { authenticated } = require('./firebase-admin-client.cjs');
(async () => {
  const project = process.argv[2];
  const api = await authenticated(project);
  const auth = api.client('https://identitytoolkit.googleapis.com');
  const current = (await auth.get(`/admin/v2/projects/${project}/config`,{skipLog:{resBody:true}})).body;
  const authorizedDomains = [...new Set([...(current.authorizedDomains||[]),`${project}.web.app`,`${project}.firebaseapp.com`,'localhost','127.0.0.1'])];
  await auth.patch(`/admin/v2/projects/${project}/config`,{authorizedDomains},{queryParams:{updateMask:'authorizedDomains'},skipLog:{reqBody:true,resBody:true}});
  const storage = api.client('https://storage.googleapis.com');
  const origins = [`https://${project}.web.app`,`https://${project}.firebaseapp.com`,'http://127.0.0.1:5173','http://localhost:5173'];
  await storage.patch(`/storage/v1/b/${project}.firebasestorage.app`,{
    cors:[{origin:origins,method:['GET','HEAD'],responseHeader:['Content-Type','Authorization','Content-Length','Range','X-Firebase-AppCheck','X-Firebase-Storage-Version','X-Goog-Upload-Protocol','X-Goog-Upload-Command'],maxAgeSeconds:3600}]
  },{skipLog:{resBody:true}});
  console.log('Authorized app domains and authenticated image CORS configured.');
})().catch(error=>{console.error(error.message);process.exitCode=1;});
