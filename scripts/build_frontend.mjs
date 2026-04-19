import { build } from 'esbuild';
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const frontendDir = path.join(root, 'frontend');
const vendorFontDir = path.join(frontendDir, 'vendor', 'fonts');

const sourceFiles = [
  'api.js',
  'data.js',
  'components/atoms.jsx',
  'components/chrome.jsx',
  'components/list.jsx',
  'components/trace.jsx',
  'components/detail.jsx',
  'components/tweaks.jsx',
  'components/app.jsx',
];

const contents = [
  'import * as React from "react";',
  'import * as ReactDOM from "react-dom/client";',
  'globalThis.React = React;',
  'globalThis.ReactDOM = ReactDOM;',
  ...sourceFiles.map((file) => {
    const abs = path.join(frontendDir, file);
    return `\n/* ${file} */\n${readFileSync(abs, 'utf8')}`;
  }),
].join('\n');

await build({
  stdin: {
    contents,
    resolveDir: root,
    sourcefile: 'frontend-entry.jsx',
    loader: 'jsx',
  },
  bundle: true,
  outfile: path.join(frontendDir, 'bundle.js'),
  format: 'iife',
  platform: 'browser',
  target: ['es2019'],
  define: { 'process.env.NODE_ENV': '"production"' },
  minify: true,
  sourcemap: false,
  legalComments: 'none',
});

mkdirSync(vendorFontDir, { recursive: true });

const fonts = [
  ['@fontsource/inter/files/inter-latin-400-normal.woff2', 'inter-latin-400-normal.woff2'],
  ['@fontsource/inter/files/inter-latin-500-normal.woff2', 'inter-latin-500-normal.woff2'],
  ['@fontsource/inter/files/inter-latin-600-normal.woff2', 'inter-latin-600-normal.woff2'],
  ['@fontsource/inter/files/inter-latin-700-normal.woff2', 'inter-latin-700-normal.woff2'],
  ['@fontsource/jetbrains-mono/files/jetbrains-mono-latin-400-normal.woff2', 'jetbrains-mono-latin-400-normal.woff2'],
  ['@fontsource/jetbrains-mono/files/jetbrains-mono-latin-500-normal.woff2', 'jetbrains-mono-latin-500-normal.woff2'],
  ['@fontsource/jetbrains-mono/files/jetbrains-mono-latin-600-normal.woff2', 'jetbrains-mono-latin-600-normal.woff2'],
];

for (const [modulePath, fileName] of fonts) {
  copyFileSync(
    path.join(root, 'node_modules', modulePath),
    path.join(vendorFontDir, fileName),
  );
}

writeFileSync(
  path.join(frontendDir, 'fonts.css'),
  `@font-face {
  font-family: "Inter";
  font-style: normal;
  font-display: swap;
  font-weight: 400;
  src: url("./vendor/fonts/inter-latin-400-normal.woff2") format("woff2");
}
@font-face {
  font-family: "Inter";
  font-style: normal;
  font-display: swap;
  font-weight: 500;
  src: url("./vendor/fonts/inter-latin-500-normal.woff2") format("woff2");
}
@font-face {
  font-family: "Inter";
  font-style: normal;
  font-display: swap;
  font-weight: 600;
  src: url("./vendor/fonts/inter-latin-600-normal.woff2") format("woff2");
}
@font-face {
  font-family: "Inter";
  font-style: normal;
  font-display: swap;
  font-weight: 700;
  src: url("./vendor/fonts/inter-latin-700-normal.woff2") format("woff2");
}
@font-face {
  font-family: "JetBrains Mono";
  font-style: normal;
  font-display: swap;
  font-weight: 400;
  src: url("./vendor/fonts/jetbrains-mono-latin-400-normal.woff2") format("woff2");
}
@font-face {
  font-family: "JetBrains Mono";
  font-style: normal;
  font-display: swap;
  font-weight: 500;
  src: url("./vendor/fonts/jetbrains-mono-latin-500-normal.woff2") format("woff2");
}
@font-face {
  font-family: "JetBrains Mono";
  font-style: normal;
  font-display: swap;
  font-weight: 600;
  src: url("./vendor/fonts/jetbrains-mono-latin-600-normal.woff2") format("woff2");
}
`,
  'utf8',
);

console.log('built frontend/bundle.js and vendored local font assets');
