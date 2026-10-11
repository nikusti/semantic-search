import { build } from 'esbuild';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
const directory=await mkdtemp(join(tmpdir(),'semantic-search-tests-'));
try {
  const output=join(directory,'runtime.test.cjs');
  await build({entryPoints:['tests/runtime.test.ts'],outfile:output,bundle:true,platform:'node',format:'cjs',target:'es2022'});
  const result=spawnSync(process.execPath,['--test',output],{stdio:'inherit'});
  process.exitCode=result.status || 0;
} finally { await rm(directory,{recursive:true,force:true}); }
