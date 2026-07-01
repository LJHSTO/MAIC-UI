import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const ROOT = path.resolve('ops/local-batch/outputs/maicui');
const MODEL_DIRS = ['qwen3.6-27b', 'qwen3.6-35b-a3b', 'glm-5', 'gemini-3.1-pro'];

function walkHtml(dir) {
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir, { withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.toLowerCase().endsWith('.html'))
    .map((entry) => path.join(dir, entry.name))
    .sort((a, b) => a.localeCompare(b, 'zh-Hans-CN'));
}

function extractScripts(html) {
  const scripts = [];
  const re = /<script\b[^>]*>([\s\S]*?)<\/script>/gi;
  let match;
  while ((match = re.exec(html))) {
    scripts.push(match[1]);
  }
  return scripts;
}

function extractHandlers(html) {
  const handlers = [];
  const re = /\s(on[a-z]+)\s*=\s*(["'])([\s\S]*?)\2/gi;
  let match;
  while ((match = re.exec(html))) {
    handlers.push({ attr: match[1], code: match[3] });
  }
  return handlers;
}

function tryParseScript(code, filename) {
  try {
    new vm.Script(code, { filename });
    return null;
  } catch (error) {
    return error && error.message ? error.message : String(error);
  }
}

function tryParseHandler(code, filename) {
  try {
    new Function(code);
    return null;
  } catch (error) {
    return error && error.message ? error.message : String(error);
  }
}

function count(re, html) {
  return (html.match(re) || []).length;
}

const rows = [];

for (const model of MODEL_DIRS) {
  for (const filePath of walkHtml(path.join(ROOT, model))) {
    const html = fs.readFileSync(filePath, 'utf8');
    const scripts = extractScripts(html);
    const handlers = extractHandlers(html);
    const scriptErrors = scripts
      .map((code, index) => tryParseScript(code, `${filePath}#script-${index + 1}`))
      .filter(Boolean);
    const handlerErrors = handlers
      .map((handler, index) => {
        const error = tryParseHandler(handler.code, `${filePath}#${handler.attr}-${index + 1}`);
        return error ? `${handler.attr}: ${error}` : null;
      })
      .filter(Boolean);

    const controls = count(/<(button|input|select|textarea)\b/gi, html);
    const row = {
      model,
      file: path.basename(filePath),
      path: filePath,
      bytes: Buffer.byteLength(html, 'utf8'),
      chars: html.length,
      scripts: scripts.length,
      handlers: handlers.length,
      controls,
      canvas: count(/<canvas\b/gi, html),
      trackEvent: html.includes('trackEvent'),
      localStorageEvents: html.includes('maic_learning_events'),
      doctype: /<!doctype html>/i.test(html),
      htmlClose: /<\/html>\s*$/i.test(html.trim()),
      bodyClose: /<\/body>/i.test(html),
      scriptErrors,
      handlerErrors,
    };
    row.status =
      scriptErrors.length || handlerErrors.length
        ? 'syntax_error'
        : row.chars < 8000 || !row.trackEvent || !row.canvas || controls < 3
          ? 'needs_regeneration'
          : 'ok';
    rows.push(row);
  }
}

const summary = rows.reduce((acc, row) => {
  acc.total += 1;
  acc[row.status] = (acc[row.status] || 0) + 1;
  acc.byModel[row.model] ||= { total: 0, ok: 0, syntax_error: 0, needs_regeneration: 0 };
  acc.byModel[row.model].total += 1;
  acc.byModel[row.model][row.status] += 1;
  return acc;
}, { total: 0, ok: 0, syntax_error: 0, needs_regeneration: 0, byModel: {} });

console.log(JSON.stringify({ summary, rows }, null, 2));
