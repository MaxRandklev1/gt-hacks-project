// Keep the app and Firebase's redirect helper on one origin for mobile browsers.
export function canonicalAppUrl(href: string, projectId: string, authDomain: string): string | null {
  const current = new URL(href);
  if (!projectId || authDomain !== `${projectId}.firebaseapp.com`
    || current.origin !== `https://${projectId}.web.app`) return null;
  current.hostname = authDomain;
  return current.href;
}

export function isStoreOrigin(candidate: string, current: string): boolean {
  if (candidate === current) return true;
  const currentUrl = new URL(current);
  const project = currentUrl.hostname.match(/^([a-z0-9-]+)\.(?:web\.app|firebaseapp\.com)$/)?.[1];
  return currentUrl.protocol === 'https:' && Boolean(project)
    && (candidate === `https://${project}.web.app` || candidate === `https://${project}.firebaseapp.com`);
}
