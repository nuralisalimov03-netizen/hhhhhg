/* Public Firebase web configuration only. The user can also paste these fields
 * in Chits Market settings. Never add a service-account JSON, private key,
 * administrator password, ID token or refresh token to this file.
 * The Firebase API key identifies a project; security is enforced by the
 * included Firestore/Storage rules and the chitsAdmin custom claim.
 */
window.CHITS_FIREBASE = window.CHITS_FIREBASE || {
  enabled: false,
  apiKey: '',
  authDomain: '',
  projectId: '',
  storageBucket: '',
  appId: '',
  adminEmail: ''
};
