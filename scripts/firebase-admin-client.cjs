// Reuse the official Firebase CLI's authenticated HTTP client. Never log tokens.
// Locked firebase-tools version is recorded in pnpm-lock.yaml.
const { getGlobalDefaultAccount } = require('firebase-tools/lib/auth');
const { requireAuth } = require('firebase-tools/lib/requireAuth');
const { Client } = require('firebase-tools/lib/apiv2');
const { ensure } = require('firebase-tools/lib/ensureApiEnabled');

async function authenticated(project) {
  if (!/^[a-z][a-z0-9-]{5,29}$/.test(project)) throw new Error('Invalid Firebase project ID');
  const account = getGlobalDefaultAccount();
  if (!account) throw new Error('Run firebase login first.');
  await requireAuth({ project, ...account });
  return { client: origin => new Client({urlPrefix: origin}), ensure: api => ensure(project, api, 'thread') };
}
module.exports = { authenticated };
