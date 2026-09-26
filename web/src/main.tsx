import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './styles.css';
import { canonicalAppUrl } from './lib/origin';

const canonicalUrl = canonicalAppUrl(location.href, import.meta.env.VITE_FIREBASE_PROJECT_ID || '', import.meta.env.VITE_FIREBASE_AUTH_DOMAIN || '');
if (canonicalUrl) {
  location.replace(canonicalUrl);
} else {
  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode><App /></React.StrictMode>,
  );
}
