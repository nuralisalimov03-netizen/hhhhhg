import { readFile } from 'node:fs/promises';
import { after, before, beforeEach, test } from 'node:test';
import { initializeTestEnvironment, assertFails, assertSucceeds } from '@firebase/rules-unit-testing';
import { doc, getDoc, getDocs, collection, setDoc, deleteDoc } from 'firebase/firestore';
import { ref, uploadBytes, getBytes, deleteObject, listAll } from 'firebase/storage';

let env;
const id = 'sample-app';
const app = {id,title:'Example',description:'Offline app',category:'Инструменты',versionName:'APK',packageName:'',size:8,sha256:'a'.repeat(64),storagePath:`apks/${id}.apk`};
const metadata = {contentType:'application/vnd.android.package-archive',customMetadata:{sha256:app.sha256}};
before(async () => {
  env = await initializeTestEnvironment({ projectId:'demo-chits-market',
    firestore:{rules:await readFile(new URL('firestore.rules',import.meta.url),'utf8')},
    storage:{rules:await readFile(new URL('storage.rules',import.meta.url),'utf8')} });
});
beforeEach(async () => { await env.clearFirestore(); await env.clearStorage(); });
after(async () => { if (env) await env.cleanup(); });

test('public can read catalog but anonymous and ordinary users cannot write', async () => {
  const anonymous=env.unauthenticatedContext(), user=env.authenticatedContext('ordinary');
  await assertSucceeds(getDocs(collection(anonymous.firestore(),'apps')));
  await assertFails(setDoc(doc(anonymous.firestore(),'apps',id),app));
  await assertFails(setDoc(doc(user.firestore(),'apps',id),app));
  await assertFails(setDoc(doc(user.firestore(),'admins','ordinary'),{chitsAdmin:true}));
});

test('admin catalog schema enforced and permissions cannot be granted by clients', async () => {
  const admin=env.authenticatedContext('admin',{chitsAdmin:true}).firestore();
  await assertSucceeds(setDoc(doc(admin,'apps',id),app));
  await assertSucceeds(getDoc(doc(env.unauthenticatedContext().firestore(),'apps',id)));
  for (const invalid of [{...app,sha256:'invalid'},{...app,size:209715201},{...app,storagePath:'private/a.apk'},{...app,unknown:'field'},{...app,id:'different'}]) {
    await assertFails(setDoc(doc(admin,'apps',id),invalid));
  }
  await assertFails(setDoc(doc(admin,'settings','admin'),{uid:'other'}));
  await assertSucceeds(deleteDoc(doc(admin,'apps',id)));
});

test('only admin can upload APK; public can retrieve known object but cannot list', async () => {
  const admin=env.authenticatedContext('admin',{chitsAdmin:true}).storage();
  const ordinary=env.authenticatedContext('ordinary').storage();
  const publicStorage=env.unauthenticatedContext().storage();
  await assertFails(uploadBytes(ref(publicStorage,app.storagePath),new Uint8Array(8),metadata));
  await assertFails(uploadBytes(ref(ordinary,app.storagePath),new Uint8Array(8),metadata));
  await assertSucceeds(uploadBytes(ref(admin,app.storagePath),new Uint8Array(8),metadata));
  await assertSucceeds(getBytes(ref(publicStorage,app.storagePath)));
  await assertFails(listAll(ref(publicStorage,'apks')));
  await assertFails(deleteObject(ref(ordinary,app.storagePath)));
  await assertSucceeds(deleteObject(ref(admin,app.storagePath)));
});

test('admin cannot upload arbitrary files, wrong MIME, or missing checksum metadata', async () => {
  const storage=env.authenticatedContext('admin',{chitsAdmin:true}).storage();
  await assertFails(uploadBytes(ref(storage,'private/file.apk'),new Uint8Array(8),metadata));
  await assertFails(uploadBytes(ref(storage,'apks/file.txt'),new Uint8Array(8),metadata));
  await assertFails(uploadBytes(ref(storage,'apks/file.apk'),new Uint8Array(8),{...metadata,contentType:'text/html'}));
  await assertFails(uploadBytes(ref(storage,'apks/file.apk'),new Uint8Array(8),{contentType:metadata.contentType}));
});
