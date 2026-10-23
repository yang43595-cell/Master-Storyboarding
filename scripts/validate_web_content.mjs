// Offline only. Import the actual website schema; no HTTP, credential or publication path.
import {readFileSync} from 'node:fs';
import {createRequire, registerHooks} from 'node:module';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';
const require = createRequire(import.meta.url);
try {
  const input = JSON.parse(readFileSync(0, 'utf8'));
  const zod = pathToFileURL(require.resolve('zod')).href;
  registerHooks({resolve(specifier, context, next) {
    return next(specifier === 'zod' ? zod : specifier, context);
  }});
  const C = await import(pathToFileURL(resolve(input.contractRoot, 'lib/content-contract.ts')).href);
  const request = C.PublishRequest.parse(input.request);
  const canonical = C.canonicalJSON(request.content);
  process.stdout.write(JSON.stringify({request, version: await C.digest(canonical), canonical}));
} catch (error) {
  process.stderr.write(String(error?.message || error));
  process.exitCode = 2;
}
