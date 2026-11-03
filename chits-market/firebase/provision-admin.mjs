#!/usr/bin/env node
/** Trusted, explicit account provisioning. No credentials are written to source. */
import { applicationDefault, initializeApp } from 'firebase-admin/app';
import { getAuth } from 'firebase-admin/auth';
import { createInterface } from 'node:readline/promises';
import { Writable } from 'node:stream';
import { pathToFileURL } from 'node:url';

export function parseOptions(args) {
  const options = {};
  for (let i = 0; i < args.length; i += 1) {
    const flag = args[i];
    if (['--reset-password', '--password-stdin', '--revoke-admin'].includes(flag)) {
      options[flag.slice(2)] = true;
    } else if (['--project', '--email'].includes(flag)) {
      if (!args[i + 1] || args[i + 1].startsWith('--')) throw new Error(`Missing ${flag} value`);
      options[flag.slice(2)] = args[++i];
    } else throw new Error(`Unknown argument: ${flag}`);
  }
  if (!/^[a-z][a-z0-9-]{4,28}[a-z0-9]$/.test(options.project || '')) throw new Error('Provide the exact Firebase project ID with --project.');
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(options.email || '')) throw new Error('Provide the administrator email with --email.');
  if (options['revoke-admin'] && options['reset-password']) throw new Error('Role revocation and password reset are separate operations.');
  return options;
}

async function readPassword(options) {
  let password = process.env.CHITS_FIREBASE_ADMIN_PASSWORD;
  if (password === undefined && options['password-stdin']) {
    const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
    for await (const line of input) { password = line; input.close(); break; }
  } else if (password === undefined && process.stdin.isTTY) {
    // readline is terminal-aware, but its output is muted while entering a secret.
    let muted = false;
    const output = new Writable({ write(chunk, encoding, callback) { if (!muted) process.stdout.write(chunk, encoding); callback(); } });
    output.isTTY = true;
    const input = createInterface({ input: process.stdin, output, terminal: true });
    process.stdout.write('Administrator password (hidden): ');
    muted = true;
    try { password = await input.question(''); } finally { input.close(); process.stdout.write('\n'); }
  }
  if (typeof password !== 'string' || password.length < 8 || password.length > 128) {
    throw new Error('Supply an 8–128 character password through hidden input, --password-stdin or CHITS_FIREBASE_ADMIN_PASSWORD.');
  }
  return password;
}

export async function provision(auth, options, passwordReader = readPassword) {
  let user;
  try { user = await auth.getUserByEmail(options.email); }
  catch (error) { if (error.code !== 'auth/user-not-found') throw error; }
  if (options['revoke-admin']) {
    if (!user) throw new Error('No account exists for this email.');
    const claims = { ...user.customClaims };
    delete claims.chitsAdmin;
    await auth.setCustomUserClaims(user.uid, claims);
    await auth.revokeRefreshTokens(user.uid);
    return { uid: user.uid, email: user.email, admin: false };
  }
  if (!user) {
    user = await auth.createUser({ email: options.email, password: await passwordReader(options), displayName: 'Leon_Salimov', disabled: false });
  } else if (options['reset-password']) {
    user = await auth.updateUser(user.uid, { password: await passwordReader(options), displayName: 'Leon_Salimov' });
    await auth.revokeRefreshTokens(user.uid);
  }
  await auth.setCustomUserClaims(user.uid, { ...user.customClaims, chitsAdmin: true });
  return { uid: user.uid, email: user.email, username: 'Leon_Salimov', admin: true };
}

async function main() {
  const options = parseOptions(process.argv.slice(2));
  if (process.env.FIREBASE_AUTH_EMULATOR_HOST || process.env.FIRESTORE_EMULATOR_HOST || process.env.STORAGE_EMULATOR_HOST) {
    throw new Error('Unset emulator variables before provisioning a real Firebase project.');
  }
  const app = initializeApp({ credential: applicationDefault(), projectId: options.project });
  const result = await provision(getAuth(app), options);
  console.log(JSON.stringify(result, null, 2));
  console.log('Sign out and sign in again to refresh the role in the Firebase ID token.');
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(error => { console.error(error.code ? `Provisioning failed (${error.code}). Check project access and Firebase Auth setup.` : error.message); process.exitCode = 1; });
}
