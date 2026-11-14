import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import ts from 'typescript';
import { File } from 'node:buffer';
// Execute the real API client; only browser storage is substituted.
globalThis.localStorage = { getItem: () => null };
const source = fs.readFileSync(new URL('./src/lib/api-client.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const modulePath = new URL('./.password-upload-client.mjs', import.meta.url);
fs.writeFileSync(modulePath, compiled);
let request;
const server = http.createServer(async (req, res) => {
  const parts = [];
  for await (const part of req) parts.push(part);
  request = { type: req.headers['content-type'], body: Buffer.concat(parts).toString() };
  res.setHeader('Content-Type', 'application/json');
  res.end('{}');
});
try {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const { apiClient } = await import(modulePath);
  // Axios instance belongs to this real client; replace only destination.
  apiClient.client.defaults.baseURL = `http://127.0.0.1:${server.address().port}`;
  await apiClient.passwordsImportFromProvider({ appleFile: new File(['Title,URL,Username,Password,Notes,OTPAuth\n'], 'synthetic.csv') });
  assert.match(request.type, /^multipart\/form-data; boundary=/);
  assert.match(request.body, /name="file"; filename="synthetic.csv"/);
  assert.match(request.body, /Title,URL,Username,Password,Notes,OTPAuth/);
  console.log('Actual API-client multipart request: PASS');
} finally {
  server.close();
  fs.unlinkSync(modulePath);
}
