'use strict';

const fs = require('node:fs');
const path = require('node:path');

// Two ordinary Playwright clicks with observation prepared before either click.
// No business API, forced click, retry or response wait is inserted between them.
async function sendThenStop({ send, stop, stopSelector = '', evidenceDir, timeoutMs = 10_000 }) {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0 || timeoutMs > 120_000) {
    throw new TypeError('sendThenStop timeoutMs must be in (0, 120000]');
  }
  if (send.page() !== stop.page()) throw new Error('Send and Stop must belong to the same page');
  let watch, sendHandle, stopHandle;
  let measurement = { observation: 'not_started', send_click_ms: null, stop_click_ms: null };
  let failure;
  try {
    sendHandle = await send.elementHandle({ timeout: timeoutMs });
    // A Stop control may be mounted only after Send. A caller-provided exact,
    // panel-scoped CSS selector lets passive observation follow that mounting.
    // Never wait for it before the action which creates it.
    if (!stopSelector) stopHandle = await stop.elementHandle({ timeout: timeoutMs });
    watch = await sendHandle.evaluateHandle((sendNode, { initialStop, selector }) => {
      const doc = sendNode.ownerDocument;
      const findStop = () => {
        if (!selector) return initialStop;
        const matches = doc.querySelectorAll(selector);
        return matches.length === 1 ? matches[0] : null;
      };
      const initial = findStop();
      if (initial && (sendNode === initial || doc !== initial.ownerDocument)) {
        throw new Error('Send and Stop must be distinct controls in one document');
      }
      const result = { observation: 'capturing', time_origin_ms: performance.timeOrigin,
        started_ms: performance.now(), send_click_ms: null, stop_click_ms: null,
        stop_dom_ready_ms: null, send_click_count: 0, stop_click_count: 0,
        stop_observations: [], stop_observations_dropped: 0, pointer_events: [] };
      let previousNode, nodeGeneration = 0, previousRect = '', stableSince = null, previousSample = '';
      function sample() {
        if (result.send_click_ms === null || result.stop_click_ms !== null) return;
        const now = performance.now();
        const stopNode = findStop();
        const connected = !!stopNode?.isConnected;
        const visible = connected && stopNode.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
        const enabled = connected && !stopNode.matches(':disabled') && stopNode.getAttribute('aria-disabled') !== 'true';
        if (visible && enabled && result.stop_dom_ready_ms === null) result.stop_dom_ready_ms = now;
        const bounds = connected ? stopNode.getBoundingClientRect() : null;
        const rect = bounds ? [bounds.x, bounds.y, bounds.width, bounds.height].map(v => Math.round(v * 4) / 4) : null;
        const rectKey = JSON.stringify(rect);
        if (stopNode !== previousNode || rectKey !== previousRect) {
          if (stopNode !== previousNode) nodeGeneration += 1;
          stableSince = now; previousNode = stopNode; previousRect = rectKey;
        }
        const hit = visible && bounds ? doc.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2) : null;
        const state = { connected, visible, enabled, rect, node_generation: nodeGeneration,
          center_hits_stop: !!hit && stopNode.contains(hit), hit_tag: hit?.tagName || null,
          rect_unchanged_since_ms: stableSince };
        const key = JSON.stringify(state);
        if (key === previousSample) return;
        previousSample = key;
        if (result.stop_observations.length < 128) result.stop_observations.push({ at_ms: now, ...state });
        else result.stop_observations_dropped += 1;
      }
      function pointer(event) {
        if (!event.isTrusted || result.pointer_events.length >= 16) return;
        const stopNode = findStop();
        const control = sendNode.contains(event.target) ? 'send' : stopNode?.contains(event.target) ? 'stop' : 'other';
        if (control === 'other' && (result.send_click_ms === null || result.stop_click_ms !== null)) return;
        sample();
        result.pointer_events.push({ type: event.type, at_ms: performance.now(), control,
          target_tag: event.target?.tagName || null, default_prevented_at_capture: event.defaultPrevented });
      }
      function clicked(event) {
        if (!event.isTrusted) return;
        if (sendNode.contains(event.target)) {
          result.send_click_count += 1;
          result.send_click_ms ??= performance.now();
          sample();
        }
        const stopNode = findStop();
        if (stopNode?.contains(event.target)) {
          sample();
          result.stop_click_count += 1;
          result.stop_click_ms ??= performance.now();
        }
      }
      const mutations = new MutationObserver(sample);
      mutations.observe(doc.documentElement, { subtree: true, attributes: true, childList: true });
      doc.addEventListener('click', clicked, true);
      doc.addEventListener('pointerdown', pointer, true);
      doc.addEventListener('pointerup', pointer, true);
      // CSS changes need not produce a mutation on the observed control itself.
      let frame;
      function tick() { sample(); frame = requestAnimationFrame(tick); }
      frame = requestAnimationFrame(tick);
      return { finish() {
        sample(); mutations.disconnect(); cancelAnimationFrame(frame);
        doc.removeEventListener('click', clicked, true);
        doc.removeEventListener('pointerdown', pointer, true);
        doc.removeEventListener('pointerup', pointer, true);
        return { ...result, observation: 'complete', ended_ms: performance.now(),
          controls_connected: sendNode.isConnected && !!findStop()?.isConnected,
          send_to_stop_ms: result.send_click_ms !== null && result.stop_click_ms !== null
            ? result.stop_click_ms - result.send_click_ms : null };
      } };
    }, { initialStop: stopHandle || null, selector: stopSelector });
    await send.click({ timeout: timeoutMs });
    await stop.click({ timeout: timeoutMs });
  } catch (error) {
    failure = error;
  } finally {
    if (watch) {
      try { measurement = await watch.evaluate(value => value.finish()); }
      catch (error) { measurement = { ...measurement, observation: 'document_lost', observation_error: error.name }; }
    }
    measurement.click_error = failure?.name || null;
    // DOM readiness is only visibility/enabled observation. The actual click
    // still waits for Playwright actionability, and all that time stays counted.
    measurement.ready_basis = 'observed_visible_and_enabled_not_full_actionability';
    measurement.observation_basis = 'passive_rect_center_hit_and_trusted_pointer_not_playwright_actionability';
    fs.appendFileSync(path.join(evidenceDir, 'tau-send-stop.jsonl'), JSON.stringify(measurement) + '\n');
    await Promise.allSettled([watch?.dispose(), sendHandle?.dispose(), stopHandle?.dispose()]);
  }
  if (failure) { failure.sendStopMeasurement = measurement; throw failure; }
  return measurement;
}

module.exports = { sendThenStop };
