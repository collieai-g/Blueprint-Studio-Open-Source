import { createHash, createHmac, randomBytes } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
// Run only against a disposable object store. A successful test leaves retained objects.
if (process.env.BLUEPRINT_ENGINE_TEST_DISPOSABLE !== '1') throw new Error('Set BLUEPRINT_ENGINE_TEST_DISPOSABLE=1 for an explicitly disposable object store');
const credentialsFile = process.env.BLUEPRINT_ENGINE_TEST_S3_CREDENTIALS_FILE;
const c = credentialsFile
  ? JSON.parse(await readFile(credentialsFile, 'utf8')).identities[0].credentials[0]
  : { accessKey: process.env.BLUEPRINT_ENGINE_TEST_S3_ACCESS_KEY, secretKey: process.env.BLUEPRINT_ENGINE_TEST_S3_SECRET_KEY };
const endpoint = process.env.BLUEPRINT_ENGINE_TEST_S3_ENDPOINT;
if (!endpoint || !c.accessKey || !c.secretKey) throw new Error('Set the test S3 endpoint and credentials (environment or Seaweed identity JSON file)');
const restartState = process.argv[2] === '--recheck' ? JSON.parse(await readFile(process.argv[3], 'utf8')) : null;
const bucket = restartState?.bucket || `blueprint-engine-${randomBytes(4).toString('hex')}`;
const hash=x=>createHash('sha256').update(x).digest('hex');
const hmac=(k,v)=>createHmac('sha256',k).update(v).digest();
const encode=s=>encodeURIComponent(s).replace(/[!'()*]/g,c=>`%${c.charCodeAt(0).toString(16).toUpperCase()}`);
async function request(method,path,query={},body='',headers={}) {
 const date=new Date().toISOString().replace(/[:-]|\.\d{3}/g,'');
 const payload=hash(body);const u=new URL(endpoint);
 const hs=Object.fromEntries(Object.entries({host:u.host,'x-amz-content-sha256':payload,'x-amz-date':date,...headers}).map(([k,v])=>[k.toLowerCase(),v]));
 const keys=Object.keys(hs).sort();const canonicalHeaders=keys.map(k=>`${k}:${hs[k].trim()}\n`).join('');
 const signedHeaders=keys.join(';');const qs=Object.entries(query).sort(([a],[b])=>a.localeCompare(b)).map(([k,v])=>`${encode(k)}=${encode(v)}`).join('&');
 const scope=`${date.slice(0,8)}/us-east-1/s3/aws4_request`;
 const canonical=[method,path,qs,canonicalHeaders,signedHeaders,payload].join('\n');
 let signing=hmac(`AWS4${c.secretKey}`,date.slice(0,8));for(const x of ['us-east-1','s3','aws4_request'])signing=hmac(signing,x);
 hs.authorization=`AWS4-HMAC-SHA256 Credential=${c.accessKey}/${scope}, SignedHeaders=${signedHeaders}, Signature=${hmac(signing,`AWS4-HMAC-SHA256\n${date}\n${scope}\n${hash(canonical)}`).toString('hex')}`;
 const r=await fetch(`${endpoint}${path}${qs?'?'+qs:''}`,{method,headers:hs,signal:AbortSignal.timeout(15000),...(body?{body}:{})});
 return {status:r.status,body:await r.text(),headers:Object.fromEntries(r.headers)};
}
const results=[];function check(name,r,predicate){if(!predicate(r))throw new Error(`${name}: status=${r.status} body=${r.body.slice(0,600)}`);results.push({name,status:r.status,version:r.headers['x-amz-version-id']??null});}
if(restartState) {
 const lock=await request('GET',`/${bucket}`,{'object-lock':''});check('ObjectLock persists across restart',lock,r=>r.status===200&&r.body.includes('Enabled'));
 const versions=await request('GET',`/${bucket}`,{versions:''});check('versions persist across restart',versions,r=>r.status===200&&r.body.includes('sample.txt'));
 const retained=await request('DELETE',`/${bucket}/sample.txt`,{versionId:restartState.version1});check('retention blocks deletion after restart',retained,r=>r.status===403);
 const old=await request('GET',`/${bucket}/sample.txt`,{versionId:restartState.version1});check('old version readable after restart',old,r=>r.status===200&&r.body==='version one');
 console.log(JSON.stringify({engine:'S3 ObjectLock',bucket,results},null,2));process.exit(0);
}
let r=await request('PUT',`/${bucket}`,{},'',{'x-amz-bucket-object-lock-enabled':'true'});check('create ObjectLock bucket',r,r=>r.status===200);
r=await request('GET',`/${bucket}`,{'object-lock':''});check('ObjectLock configured',r,r=>r.status===200&&r.body.includes('Enabled'));
r=await request('PUT',`/${bucket}`,{versioning:''},'<VersioningConfiguration xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Status>Enabled</Status></VersioningConfiguration>',{'content-type':'application/xml'});check('enable versioning',r,r=>r.status===200);
const retainUntil=new Date(Date.now()+3600000).toISOString();
r=await request('PUT',`/${bucket}/sample.txt`,{},'version one',{'x-amz-object-lock-mode':'GOVERNANCE','x-amz-object-lock-retain-until-date':retainUntil});check('upload retained version',r,r=>r.status===200&&!!r.headers['x-amz-version-id']);const version1=r.headers['x-amz-version-id'];
r=await request('GET',`/${bucket}/sample.txt`,{versionId:version1});check('read version1',r,r=>r.status===200&&r.body==='version one');
r=await request('PUT',`/${bucket}/sample.txt`,{},'version two');check('upload version2',r,r=>r.status===200&&!!r.headers['x-amz-version-id']&&r.headers['x-amz-version-id']!==version1);const version2=r.headers['x-amz-version-id'];
r=await request('GET',`/${bucket}/sample.txt`,{versionId:version1});check('old version still readable',r,r=>r.status===200&&r.body==='version one');
r=await request('DELETE',`/${bucket}/sample.txt`,{versionId:version1});check('governance blocks permanent version delete',r,r=>r.status===403);
r=await request('PUT',`/${bucket}/sample.txt`,{'legal-hold':'',versionId:version2},'<LegalHold xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Status>ON</Status></LegalHold>',{'content-type':'application/xml'});check('set legal hold ON',r,r=>r.status===200);
r=await request('DELETE',`/${bucket}/sample.txt`,{versionId:version2});check('legal hold blocks permanent version delete',r,r=>r.status===403);
r=await request('PUT',`/${bucket}/sample.txt`,{'legal-hold':'',versionId:version2},'<LegalHold xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Status>OFF</Status></LegalHold>',{'content-type':'application/xml'});check('set legal hold OFF',r,r=>r.status===200);
r=await request('GET',`/${bucket}/sample.txt`);check('current version content',r,r=>r.status===200&&r.body==='version two');
const out={engine:'S3 ObjectLock',captured_at:new Date().toISOString(),bucket,version1,version2,retainUntil,results};
if (process.env.BLUEPRINT_ENGINE_TEST_RESULT_FILE) await writeFile(process.env.BLUEPRINT_ENGINE_TEST_RESULT_FILE,JSON.stringify(out,null,2));
console.log(JSON.stringify(out,null,2));
