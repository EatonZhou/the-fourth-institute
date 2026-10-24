'use strict';

const fs = require('node:fs');
const path = require('node:path');

// Two ordinary Playwright clicks with observation prepared before either click.
// No business API, forced click, retry or response wait is inserted between them.
async function sendThenStop({ send, stop, evidenceDir, timeoutMs = 10_000 }) {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0 || timeoutMs > 120_000) {
    throw new TypeError('sendThenStop timeoutMs must be in (0, 120000]');
  }
  if (send.page() !== stop.page()) throw new Error('Send and Stop must belong to the same page');
  const sendHandle = await send.elementHandle({ timeout: timeoutMs });
  const stopHandle = await stop.elementHandle({ timeout: timeoutMs });
  let watch;
  let measurement = { observation: 'not_started', send_click_ms: null, stop_click_ms: null };
  let failure;
  try {
    watch = await sendHandle.evaluateHandle((sendNode, stopNode) => {
      if (sendNode === stopNode || sendNode.ownerDocument !== stopNode.ownerDocument) {
        throw new Error('Send and Stop must be distinct controls in one document');
      }
      const doc = sendNode.ownerDocument;
      const result = { observation: 'capturing', time_origin_ms: performance.timeOrigin,
        started_ms: performance.now(), send_click_ms: null, stop_click_ms: null,
        stop_dom_ready_ms: null, send_click_count: 0, stop_click_count: 0 };
      function sample() {
        if (result.send_click_ms === null || result.stop_dom_ready_ms !== null) return;
        const visible = stopNode.isConnected && stopNode.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
        if (visible && !stopNode.matches(':disabled') && stopNode.getAttribute('aria-disabled') !== 'true') {
          result.stop_dom_ready_ms = performance.now();
        }
      }
      function clicked(event) {
        if (!event.isTrusted) return;
        if (sendNode.contains(event.target)) {
          result.send_click_count += 1;
          result.send_click_ms ??= performance.now();
          sample();
        }
        if (stopNode.contains(event.target)) {
          result.stop_click_count += 1;
          result.stop_click_ms ??= performance.now();
        }
      }
      const mutations = new MutationObserver(sample);
      mutations.observe(doc.documentElement, { subtree: true, attributes: true, childList: true });
      doc.addEventListener('click', clicked, true);
      // CSS changes need not produce a mutation on the observed control itself.
      let frame;
      function tick() { sample(); frame = requestAnimationFrame(tick); }
      frame = requestAnimationFrame(tick);
      return { finish() {
        sample(); mutations.disconnect(); cancelAnimationFrame(frame);
        doc.removeEventListener('click', clicked, true);
        return { ...result, observation: 'complete', ended_ms: performance.now(),
          controls_connected: sendNode.isConnected && stopNode.isConnected,
          send_to_stop_ms: result.send_click_ms !== null && result.stop_click_ms !== null
            ? result.stop_click_ms - result.send_click_ms : null };
      } };
    }, stopHandle);
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
    fs.appendFileSync(path.join(evidenceDir, 'tau-send-stop.jsonl'), JSON.stringify(measurement) + '\n');
    await Promise.allSettled([watch?.dispose(), sendHandle.dispose(), stopHandle.dispose()]);
  }
  if (failure) { failure.sendStopMeasurement = measurement; throw failure; }
  return measurement;
}

module.exports = { sendThenStop };
