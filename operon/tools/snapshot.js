// Page perception for the operator: renders the visible page as compact, structured text.
// Every interactive element gets a ref ([e1], [e2], ...) stored in a data-op-ref attribute,
// which the browser tools use to act on it. Refs are reassigned on every snapshot.
(args) => {
  const maxChars = (args && args.maxChars) || 14000;
  const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'SVG', 'HEAD', 'META', 'LINK', 'OPTION', 'LEGEND']);
  const INTERACTIVE_ROLES = new Set(['button', 'link', 'checkbox', 'radio', 'tab', 'menuitem', 'switch', 'combobox', 'textbox']);
  const FORM_CONTROLS = new Set(['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON']);
  document.querySelectorAll('[data-op-ref]').forEach((e) => e.removeAttribute('data-op-ref'));
  let counter = 0;
  const lines = [];

  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const clip = (s, n) => (s.length > n ? s.slice(0, n - 1) + '…' : s);
  const q = (s) => '"' + clip(norm(s), 140).replace(/"/g, "'") + '"';

  function visible(el) {
    if (el.hidden) return false;
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') return false;
    if (st.display !== 'contents' && el.getClientRects().length === 0) return false;
    return true;
  }

  function isBlock(el) {
    const d = getComputedStyle(el).display;
    return !d.startsWith('inline') && d !== 'contents' && d !== 'none';
  }

  function isInteractive(el) {
    const tag = el.tagName;
    if (tag === 'A') return el.hasAttribute('href');
    if (tag === 'INPUT') return (el.getAttribute('type') || '').toLowerCase() !== 'hidden';
    if (['BUTTON', 'SELECT', 'TEXTAREA', 'SUMMARY'].includes(tag)) return true;
    const role = el.getAttribute('role');
    if (role && INTERACTIVE_ROLES.has(role)) return true;
    return el.hasAttribute('onclick');
  }

  function textOf(el) {
    return norm(el.innerText || el.textContent || '');
  }

  function accName(el) {
    const aria = el.getAttribute('aria-label');
    if (aria) return aria;
    const labelledby = el.getAttribute('aria-labelledby');
    if (labelledby) {
      const t = labelledby.split(/\s+/).map((id) => document.getElementById(id)).filter(Boolean).map(textOf).join(' ');
      if (t) return t;
    }
    const tag = el.tagName;
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'A' || tag === 'BUTTON' || tag === 'SUMMARY' || el.getAttribute('role') === 'button') {
      const t = textOf(el);
      if (t) return t;
    }
    if (tag === 'INPUT' && ['submit', 'button', 'reset'].includes(type)) return el.value || type;
    if (el.id) {
      const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (label) return textOf(label);
    }
    const wrapping = el.closest('label');
    if (wrapping) return textOf(wrapping);
    if (el.placeholder) return el.placeholder;
    if (el.title) return el.title;
    const cell = el.closest('td, th');
    if (cell && cell.previousElementSibling) {
      const t = textOf(cell.previousElementSibling);
      if (t) return t;
    }
    const img = el.querySelector && el.querySelector('img[alt]');
    if (img) return img.alt;
    return el.getAttribute('name') || '';
  }

  function shortHref(a) {
    try {
      const u = new URL(a.href, location.href);
      return u.origin === location.origin ? u.pathname + u.search : u.href;
    } catch (e) {
      return a.getAttribute('href');
    }
  }

  function describe(el) {
    const ref = 'e' + ++counter;
    el.setAttribute('data-op-ref', ref);
    const tag = el.tagName;
    const type = (el.getAttribute('type') || '').toLowerCase();
    const name = accName(el);
    const dis = el.disabled ? ' disabled' : '';
    if (tag === 'A') {
      const href = el.getAttribute('href') || '';
      return `[${ref}] link ${q(name)}` + (href && !href.startsWith('javascript') ? ` -> ${shortHref(el)}` : '');
    }
    if (tag === 'BUTTON' || (tag === 'INPUT' && ['submit', 'button', 'reset', 'image'].includes(type)) ||
        el.getAttribute('role') === 'button') {
      return `[${ref}] button ${q(name)}${dis}`;
    }
    if (tag === 'SUMMARY') {
      const open = el.parentElement && el.parentElement.open;
      return `[${ref}] disclosure ${q(name)} (${open ? 'expanded' : 'collapsed — click to expand'})`;
    }
    if (tag === 'INPUT' && type === 'checkbox') return `[${ref}] checkbox ${q(name)} ${el.checked ? '[x]' : '[ ]'}${dis}`;
    if (tag === 'INPUT' && type === 'radio') return `[${ref}] radio ${q(name)} ${el.checked ? '(selected)' : '( )'}${dis}`;
    if (tag === 'INPUT' && type === 'password') return `[${ref}] password ${q(name)} ${el.value ? '(filled)' : '(empty)'}`;
    if (tag === 'SELECT') {
      const opts = Array.from(el.options).map((o) => norm(o.text));
      const sel = el.selectedIndex >= 0 ? norm(el.options[el.selectedIndex].text) : '';
      const shown = opts.slice(0, 40).map(q).join(', ') + (opts.length > 40 ? `, … (${opts.length} options)` : '');
      return `[${ref}] select ${q(name)} selected=${q(sel)} options=[${shown}]${dis}`;
    }
    if (tag === 'TEXTAREA') return `[${ref}] textarea ${q(name)} value=${q(el.value)}${dis}`;
    if (tag === 'INPUT') {
      const t = type && type !== 'text' ? ` type=${type}` : '';
      const ph = el.placeholder ? ` placeholder=${q(el.placeholder)}` : '';
      return `[${ref}] textbox ${q(name)}${t} value=${q(el.value)}${ph}${el.required ? ' required' : ''}${dis}`;
    }
    return `[${ref}] ${el.getAttribute('role') || tag.toLowerCase()} ${q(name)}`;
  }

  // A <label for=...> is rendered through its control's name; a wrapping label renders its controls only.
  function labelControls(label) {
    if (label.htmlFor) return [];
    return Array.from(label.querySelectorAll('input, select, textarea, button')).filter(visible);
  }

  function inline(el, out) {
    for (const node of el.childNodes) {
      if (node.nodeType === 3) {
        const t = norm(node.textContent);
        if (t) out.push(t);
        continue;
      }
      if (node.nodeType !== 1 || SKIP.has(node.tagName) || !visible(node)) continue;
      if (node.tagName === 'LABEL') {
        labelControls(node).forEach((c) => out.push(describe(c)));
        continue;
      }
      if (isInteractive(node)) {
        out.push(describe(node));
        continue;
      }
      inline(node, out);
    }
    return out;
  }

  const inlineText = (el) => inline(el, []).join(' ');

  function hasBlockChild(el) {
    for (const c of el.children) {
      if (!SKIP.has(c.tagName) && visible(c) && isBlock(c)) return true;
    }
    return false;
  }

  function renderTable(table, ind) {
    if (table.caption) lines.push(ind + 'table: ' + textOf(table.caption));
    let n = 0;
    const rows = Array.from(table.rows).filter(visible);
    for (const row of rows) {
      const cells = Array.from(row.cells).map((c) => inlineText(c));
      lines.push(ind + '| ' + cells.join(' | ') + ' |');
      if (++n >= 150 && rows.length > n) {
        lines.push(ind + `| … ${rows.length - n} more rows not shown |`);
        break;
      }
    }
  }

  function renderDl(dl, ind) {
    let key = null;
    for (const c of dl.children) {
      if (!visible(c)) continue;
      if (c.tagName === 'DT') key = inlineText(c);
      else if (c.tagName === 'DD') lines.push(ind + (key ? key + ': ' : '') + inlineText(c));
    }
  }

  function walk(el, depth) {
    const tag = el.tagName;
    if (SKIP.has(tag) || !visible(el)) return;
    const ind = '  '.repeat(Math.min(depth, 6));
    const alert = el.getAttribute('role') === 'alert' ? '[alert] ' : '';
    if (/^H[1-6]$/.test(tag)) {
      lines.push(ind + '#'.repeat(Number(tag[1])) + ' ' + inlineText(el));
      return;
    }
    if (tag === 'TABLE') return renderTable(el, ind);
    if (tag === 'DL') return renderDl(el, ind);
    if (tag === 'LABEL') {
      labelControls(el).forEach((c) => lines.push(ind + describe(c)));
      return;
    }
    if (isInteractive(el) && !hasBlockChild(el)) {
      lines.push(ind + describe(el));
      return;
    }
    let childDepth = depth;
    if (tag === 'FORM') {
      let action = el.getAttribute('action') || location.pathname + location.search;
      try { action = new URL(el.action, location.href).pathname; } catch (e) { /* keep raw */ }
      lines.push(ind + `form (${(el.getAttribute('method') || 'get').toUpperCase()} ${action}):`);
      childDepth = depth + 1;
    } else if (tag === 'FIELDSET') {
      const legend = el.querySelector('legend');
      lines.push(ind + `group ${q(legend ? textOf(legend) : '')}:`);
      childDepth = depth + 1;
    } else if (tag === 'DIALOG' && el.open) {
      lines.push(ind + 'dialog:');
      childDepth = depth + 1;
    }
    const prefix = (tag === 'LI' ? '- ' : '') + alert;
    if (!hasBlockChild(el)) {
      const t = inlineText(el);
      if (t) lines.push(ind + prefix + t);
      return;
    }
    let buffer = [];
    const flush = () => {
      const t = buffer.join(' ').trim();
      if (t) lines.push('  '.repeat(Math.min(childDepth, 6)) + prefix + t);
      buffer = [];
    };
    for (const node of el.childNodes) {
      if (node.nodeType === 3) {
        const t = norm(node.textContent);
        if (t) buffer.push(t);
        continue;
      }
      if (node.nodeType !== 1 || SKIP.has(node.tagName) || !visible(node)) continue;
      if (isBlock(node) || ['TABLE', 'FORM', 'DL'].includes(node.tagName)) {
        flush();
        walk(node, childDepth);
      } else if (node.tagName === 'LABEL') {
        flush();
        labelControls(node).forEach((c) => lines.push('  '.repeat(Math.min(childDepth, 6)) + describe(c)));
      } else if (FORM_CONTROLS.has(node.tagName)) {
        // Form controls get a line each so a field is never confused with its neighbour.
        flush();
        lines.push('  '.repeat(Math.min(childDepth, 6)) + describe(node));
      } else if (isInteractive(node)) {
        buffer.push(describe(node));
      } else {
        buffer.push(inlineText(node));
      }
    }
    flush();
  }

  walk(document.body, 0);
  let text = lines.filter((l) => l.trim()).join('\n');
  let truncated = false;
  if (text.length > maxChars) {
    truncated = true;
    text = text.slice(0, maxChars) + `\n… (page truncated: ${text.length - maxChars} more characters; use the filter option to search the page)`;
  }
  return { url: location.href, title: document.title, text, refs: counter, truncated };
}
