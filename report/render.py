# -*- coding: utf-8 -*-
"""SVG -> PNG. 서식이 요구하는 300dpi 이상을 맞추려고 3배로 렌더링한다."""
import sys, os, asyncio, glob
sys.path.insert(0, "/tmp/claude-0/-home-user-GML-SOURCECODE2/1c9495aa-64db-5a08-bf57-b6f842886010/scratchpad/node_modules")

CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
SCALE = 3

import subprocess, json, tempfile
names = sys.argv[1:] or [os.path.basename(p)[:-4] for p in glob.glob("FIG_*.svg")]
js = r'''
const { chromium } = require('playwright-core');
(async () => {
  const files = process.argv.slice(2);
  const b = await chromium.launch({ executablePath: process.env.CHROME });
  for (const f of files) {
    const svg = require('fs').readFileSync(f + '.svg', 'utf8');
    const m = svg.match(/viewBox="0 0 (\d+) (\d+)"/);
    const w = +m[1], h = +m[2], s = +process.env.SCALE;
    const p = await b.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: s });
    await p.setContent('<style>html,body{margin:0;padding:0}</style>' + svg);
    await p.waitForTimeout(250);
    await p.screenshot({ path: f + '.png' });
    await p.close();
    console.log('  ' + f + '.png  ' + (w*s) + 'x' + (h*s));
  }
  await b.close();
})();
'''
with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
    fh.write(js); path = fh.name
env = dict(os.environ, CHROME=CHROME, SCALE=str(SCALE),
           NODE_PATH="/tmp/claude-0/-home-user-GML-SOURCECODE2/1c9495aa-64db-5a08-bf57-b6f842886010/scratchpad/node_modules")
subprocess.run(["node", path] + names, check=True, env=env)
os.unlink(path)
