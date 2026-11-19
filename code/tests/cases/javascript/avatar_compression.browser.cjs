'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const root = path.resolve(__dirname, '../../..');
const playwright = require(process.env.PORTAL_UI_PLAYWRIGHT || path.join(root, 'management_portal/test_framework/tau_runtime/node_modules/playwright'));
const fixtures = JSON.parse(execFileSync(process.env.PORTAL_TEST_PYTHON || 'python3', ['-c', `
import base64, io, json, random
from PIL import Image, PngImagePlugin
result = {}
def add(name, image, fmt, **kwargs):
    stream = io.BytesIO()
    image.save(stream, fmt, **kwargs)
    result[name] = base64.b64encode(stream.getvalue()).decode()
noise = Image.frombytes('RGB', (1200, 900), random.Random(1).randbytes(1200*900*3)).convert('RGBA')
noise.putalpha(128)
metadata = PngImagePlugin.PngInfo()
metadata.add_text('private-original', 'never-upload-this-metadata')
add('large', noise, 'PNG', pnginfo=metadata)
exif = Image.Exif()
exif[274] = 6
add('rotated', Image.new('RGB', (600, 400), 'red'), 'JPEG', exif=exif)
add('webp', Image.new('RGBA', (600, 300), (20, 50, 90, 128)), 'WEBP', lossless=True)
add('tiny', Image.new('RGBA', (1, 1), (20, 50, 90, 128)), 'PNG')
for fmt in ('PNG', 'WEBP'):
    add('animated'+fmt, Image.new('RGBA', (32, 32), 'red'), fmt, save_all=True,
        append_images=[Image.new('RGBA', (32, 32), 'blue')], duration=100, loop=0)
print(json.dumps(result))
`], {maxBuffer: 8 * 1024 * 1024}));
const input = (key, type) => ({name: key + '.image', mimeType: type, buffer: Buffer.from(fixtures[key], 'base64')});

(async () => {
  for (const engine of ['chromium', 'webkit']) {
    const browser = await playwright[engine].launch({headless: true});
    try {
      const page = await browser.newPage();
      page.setDefaultTimeout(5000);
      const errors = []; page.on('pageerror', error => errors.push(error.message));
      await page.setContent('<main id="portrait"></main><section id="editor"></section>');
      await page.evaluate(() => {
        window.uploads = []; window.saved = []; window.failSave = false;
        const tag = (parts, ...values) => parts.reduce((out, part, i) => out + part + (values[i] ?? ''), '');
        window.PortalI18n = {t: value => value, html: tag, text: tag};
        window.PortalUIPrimitives = {loadAsset: async () => {}};
        window.PortalResidentAvatar = {markup: () => '<span>头像</span>'};
        window.PortalAPI = {get: async () => ({sprites: []})};
        window.PortalInstructionClient = {upload: async (file, options) => {
          const bitmap = await createImageBitmap(file);
          const canvas = document.createElement('canvas'); canvas.width = bitmap.width; canvas.height = bitmap.height;
          const context = canvas.getContext('2d'); context.drawImage(bitmap, 0, 0);
          const bytes = await file.arrayBuffer();
          uploads.push({name: file.name, type: file.type, size: file.size, width: bitmap.width, height: bitmap.height,
            alpha: context.getImageData(0, 0, 1, 1).data[3], originalMetadata: new TextDecoder().decode(bytes).includes('private-original'), options});
          bitmap.close();
          return {upload_id: 'upload_fixture'};
        }};
        window.options = {portrait: document.querySelector('#portrait'), load: async () => null, save: async change => {
          if (failSave) throw Error('temporary save failure'); saved.push(change); return {kind: 'sprite', sprite_index: 0};
        }};
      });
      await page.addScriptTag({path: path.join(root, 'management_portal/static/portal_avatar_editor.js')});
      await page.evaluate(() => {window.editor = PortalAvatarEditor.mount(document.querySelector('#editor'), options);});
      const pick = async file => {
        if (await page.locator('[data-avatar-open]').isVisible()) await page.locator('[data-avatar-open]').click();
        await page.locator('[data-avatar-file]').setInputFiles(file);
      };
      const save = () => page.locator('[data-avatar-save]').click();
      const status = page.locator('[data-avatar-status]');
      assert.ok(input('large', 'image/png').buffer.length > 2 * 1024 * 1024);
      for (const [key, type, width, height, alpha] of [
        ['large', 'image/png', 256, 192, 128], ['rotated', 'image/jpeg', 171, 256, 255],
        ['webp', 'image/webp', 256, 128, 128], ['tiny', 'image/png', 1, 1, 128],
      ]) {
        const before = await page.evaluate(() => saved.length);
        await pick(input(key, type));
        assert.equal(await page.evaluate(() => uploads.length), before, 'selection remains local');
        await save(); await page.waitForFunction(n => saved.length === n + 1, before);
        const upload = await page.evaluate(() => uploads.at(-1));
        assert.deepEqual([upload.width, upload.height, upload.alpha], [width, height, alpha]);
        assert.equal(upload.name, 'avatar.png'); assert.equal(upload.type, 'image/png');
        assert.ok(upload.size < 300000); assert.equal(upload.originalMetadata, false);
        assert.deepEqual(upload.options, {purpose: 'avatar'});
      }
      assert.doesNotMatch(await page.locator('#editor').textContent(), /2 MiB/);
      const baseline = await page.evaluate(() => uploads.length);
      for (const file of [input('animatedPNG', 'image/png'), input('animatedWEBP', 'image/webp'),
        {name: 'corrupt.png', mimeType: 'image/png', buffer: Buffer.from('not an image')}]) {
        await pick(file); await save(); await status.filter({hasText: '保存失败'}).waitFor();
        assert.equal(await page.evaluate(() => uploads.length), baseline, 'invalid originals never upload');
        assert.equal(await page.evaluate(() => editor.hasUnsavedChanges()), true);
      }
      // Encoding failure cannot fall back to uploading the original file.
      await page.evaluate(() => {window.toBlob = HTMLCanvasElement.prototype.toBlob; HTMLCanvasElement.prototype.toBlob = function(cb) {cb(null);};});
      await pick(input('tiny', 'image/png')); await save(); await status.filter({hasText: '图片压缩失败'}).waitFor();
      assert.equal(await page.evaluate(() => uploads.length), baseline);
      await page.evaluate(() => {HTMLCanvasElement.prototype.toBlob = toBlob; failSave = true;});
      await save(); await status.filter({hasText: 'temporary save failure'}).waitFor();
      assert.equal(await page.evaluate(() => editor.hasUnsavedChanges()), true);
      await page.evaluate(() => {failSave = false;});
      await save(); await page.locator('[data-avatar-editor]').waitFor({state: 'hidden'});
      // A destroyed host must not start an upload after a slow local decode.
      const beforeDestroy = await page.evaluate(() => uploads.length);
      await page.evaluate(() => {
        window.decode = window.createImageBitmap;
        window.createImageBitmap = async (...args) => {const bitmap = await decode(...args); await new Promise(resolve => {window.finishDecode = resolve;}); return bitmap;};
      });
      await pick(input('tiny', 'image/png')); await save();
      await page.waitForFunction(() => !!window.finishDecode);
      assert.equal(await page.evaluate(() => editor.isBusy()), true);
      await page.evaluate(() => {editor.destroy(); finishDecode();});
      await page.waitForFunction(() => !editor.isBusy());
      assert.equal(await page.evaluate(() => uploads.length), beforeDestroy);
      assert.deepEqual(errors, []);
      console.log('PASS ' + engine + ': large originals stay local; resize, alpha, EXIF orientation, formats, rejection, retry and disposal');
    } finally {await browser.close();}
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
