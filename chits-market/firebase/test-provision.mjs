import assert from 'node:assert/strict';
import test from 'node:test';
import { parseOptions, provision } from './provision-admin.mjs';

test('requires explicit project/email and rejects inline password arguments', () => {
  assert.throws(() => parseOptions(['--project','chits-demo']));
  assert.throws(() => parseOptions(['--project','chits-demo','--email','admin@example.test','--password','secret']));
  assert.equal(parseOptions(['--project','chits-demo','--email','admin@example.test']).project,'chits-demo');
});

test('creates account and grants only requested role while preserving claims', async () => {
  let role, created;
  const auth = {
    async getUserByEmail() { throw {code:'auth/user-not-found'}; },
    async createUser(data) { created = data; return {uid:'new-id',email:data.email,customClaims:{existing:true}}; },
    async setCustomUserClaims(uid, claims) { role = {uid,claims}; }
  };
  const result = await provision(auth,{email:'admin@example.test'},async() => 'test-only-password');
  assert.equal(created.displayName,'Leon_Salimov');
  assert.deepEqual(role,{uid:'new-id',claims:{existing:true,chitsAdmin:true}});
  assert.equal(result.admin,true);
  assert.ok(!('password' in result));
});

test('existing account password is unchanged without explicit reset', async () => {
  const auth = {
    async getUserByEmail() { return {uid:'old-id',email:'admin@example.test',customClaims:{existing:true}}; },
    async setCustomUserClaims(uid, claims) { assert.equal(uid,'old-id'); assert.equal(claims.existing,true); }
  };
  await provision(auth,{email:'admin@example.test'},async() => { throw new Error('Password should not be read'); });
});

test('revocation removes role, preserves unrelated claims and revokes sessions', async () => {
  let revoked = false;
  const auth = {
    async getUserByEmail() { return {uid:'admin-id',email:'admin@example.test',customClaims:{chitsAdmin:true,other:1}}; },
    async setCustomUserClaims(uid, claims) { assert.deepEqual(claims,{other:1}); },
    async revokeRefreshTokens(uid) { assert.equal(uid,'admin-id'); revoked = true; }
  };
  const result = await provision(auth,{email:'admin@example.test','revoke-admin':true});
  assert.equal(result.admin,false);
  assert.ok(revoked);
});
