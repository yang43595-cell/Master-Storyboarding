'use strict';

// Self-contained, read-only DOM callback; also usable by a supported browser
// locator.evaluate(readRichEditor). No backend calls, writes or refresh claims.
function readRichEditor(root) {
  const result = {status: 'unchecked', production_ready: false,
    coverage: {serialization: 'unchecked', native_nodes: 'unchecked',
      media_identity: 'unchecked', prompt: 'unchecked', parameters: 'unchecked', refresh: 'unchecked'},
    tokens: [], native_mentions: [], errors: []};
  try {
    if (root.localName !== 'div' || root.getAttribute('data-set-richtype') !== 'richWrap'
        || root.getAttribute('contenteditable') !== 'true' || root.getAttribute('role') !== 'textbox') {
      throw new Error('unsupported rich editor root');
    }
    let visited = 0;
    function walk(node, depth = 0) {
      if (++visited > 10000 || depth > 100) throw new Error('editor DOM exceeds bounded read');
      if (node.nodeType === 3) {
        // Adjacent text-node splitting is not a content change. No trim or
        // whitespace/FEFF normalization: those characters may be real prose.
        const previous = result.tokens[result.tokens.length - 1];
        if (previous && previous.kind === 'text') previous.value += node.data;
        else result.tokens.push({kind: 'text', value: node.data});
        return;
      }
      if (node.nodeType !== 1) throw new Error('unsupported DOM node');
      const richType = node.getAttribute('data-set-richtype');
      if (richType === 'richCustom') {
        if (node.localName !== 'span' || node.getAttribute('contenteditable') !== 'false') {
          throw new Error('reference must be a noneditable span');
        }
        const refs = Array.from(node.querySelectorAll('[data-reference-id]'));
        if (refs.length !== 1 || node.querySelectorAll('[data-set-richtype="richCustom"]').length
            || refs[0].localName !== 'span' || refs[0].closest('[contenteditable="false"]') !== node) {
          throw new Error('ambiguous reference ownership');
        }
        const ref = refs[0];
        function inspectPresentation(parent, level) {
          if (level > 100) throw new Error('reference DOM exceeds bounded read');
          for (const child of parent.childNodes) {
            if (++visited > 10000) throw new Error('reference DOM exceeds bounded read');
            if (child.nodeType === 3) continue;
            if (child.nodeType !== 1 || !['span', 'img'].includes(child.localName)
                || child.hasAttribute('contenteditable') || child.hasAttribute('data-set-richtype')
                || (child !== ref && child.hasAttribute('data-value'))
                || (child.localName === 'img' && child.childNodes.length)) {
              throw new Error('unsupported reference presentation');
            }
            inspectPresentation(child, level + 1);
          }
        }
        inspectPresentation(node, depth + 1);
        const identity = JSON.parse(decodeURIComponent(ref.getAttribute('data-reference-id')));
        const value = ref.getAttribute('data-value');
        if (!Array.isArray(identity) || identity.length !== 2 || typeof identity[0] !== 'string'
            || !identity[0].trim() || !['image', 'main'].includes(identity[1])
            || !(ref.getAttribute('class') || '').split(/\s+/).includes('imageReference')
            || typeof value !== 'string' || !/^\{\{ref [1-9]\d*\}\}$/.test(value)) {
          throw new Error('unsupported reference identity or token');
        }
        // The native tuple stores node + reference port (image or observed main),
        // not an opaque media ID. The imageReference DOM supplies rendering kind.
        const mention = {node_id: identity[0], reference_port: identity[1],
          entity_type: 'image', native_noneditable_entity: true};
        result.native_mentions.push(mention);
        result.tokens.push({kind: 'reference', ...mention, value, display_text: node.textContent});
        // Presentation descendants (including volatile blob thumbnails and
        // CSS-induced innerText newlines) are not media identity. Preserve exact
        // textContent separately so label edits/extra text cannot disappear.
        return;
      }
      // Observed redacted editor structure: paragraphs contain richMark/richWrite
      // spans. Keep their structural tokens/text, never flatten whitespace.
      const proseTypes = {richGrid: 'p', richMark: 'span', richWrite: 'span'};
      if ((richType && node !== root && proseTypes[richType] !== node.localName) || node.hasAttribute('data-reference-id')
          || (node !== root && node.hasAttribute('contenteditable'))) {
        throw new Error('unowned or unsupported rich-text entity');
      }
      if (!['div', 'p', 'span', 'br'].includes(node.localName)) throw new Error('unsupported editor element');
      if (node.localName === 'br') { result.tokens.push({kind: 'break'}); return; }
      if (node !== root) result.tokens.push({kind: 'open', tag: node.localName});
      for (const child of node.childNodes) walk(child, depth + 1);
      if (node !== root) result.tokens.push({kind: 'close', tag: node.localName});
    }
    walk(root);
    if (JSON.stringify(result).length > 100000) throw new Error('editor output exceeds bounded read');
    result.coverage.serialization = 'collected';
    result.coverage.native_nodes = 'collected';
  } catch (error) {
    // Never return a partial token/reference list that looks like full coverage.
    result.tokens = []; result.native_mentions = [];
    result.errors.push(String(error.message || error));
  }
  return result;
}
module.exports = {readRichEditor};
