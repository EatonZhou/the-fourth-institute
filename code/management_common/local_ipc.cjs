// Node byte-stream counterpart of local_ipc.py. No application messages are
// admitted until the same mutual authentication protocol has completed.
const net = require('node:net');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {execFileSync} = require('node:child_process');

const proof = (secret, role, nonce) => crypto.createHmac('sha256', secret).update(role).update(nonce).digest();
function metadata(operation, logical, record, python) {
  const result = execFileSync(python || process.env.PORTAL_PYTHON || 'python',
    [path.join(__dirname, 'local_ipc.py'), operation, logical],
    {input: record ? JSON.stringify(record) : '', encoding: 'utf8', timeout: 10000, windowsHide: true});
  return result ? JSON.parse(result) : undefined;
}

// Keep the byte limits and generation-name allowance aligned with
// local_ipc.py:local_transport. Clients and servers must make the same choice.
function defaultTCP(logical) {
  if (process.platform === 'win32') return true;
  const limit = {darwin: 104, linux: 108}[process.platform];
  if (!limit) throw new Error('Unsupported local IPC platform: ' + process.platform);
  const generation = path.join(path.dirname(logical), '.' + '0'.repeat(16));
  return Math.max(Buffer.byteLength(logical), Buffer.byteLength(generation)) >= limit;
}

function listenLocal(server, logical, {python, tcp = defaultTCP(logical)} = {}) {
  if (!tcp) {
    // libuv unlinks the path passed to listen() when closing. Give each
    // generation its own socket so retiring one cannot unlink its successor.
    const owned = path.join(path.dirname(logical), '.' + crypto.randomBytes(8).toString('hex'));
    try {
      const existing = fs.lstatSync(logical);
      const target = existing.isSymbolicLink() ? fs.readlinkSync(logical) : '';
      if (!existing.isSocket() && !(existing.isSymbolicLink() && /^\.[a-f0-9]{16}$/.test(target)))
        throw new Error('Refusing to replace a non-socket IPC path');
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
    server.listen(owned, () => {
      const link = owned + '.link';
      try {
        fs.chmodSync(owned, 0o600);
        fs.symlinkSync(path.basename(owned), link);
        fs.renameSync(link, logical);
      } catch (error) {
        try { fs.unlinkSync(link); } catch (cleanup) { if (cleanup.code !== 'ENOENT') server.emit('error', cleanup); }
        server.close(); server.emit('error', error);
      }
    });
    server.once('close', () => {
      try {
        if (fs.readlinkSync(logical) === path.basename(owned)) fs.unlinkSync(logical);
      } catch (error) { if (!['ENOENT', 'EINVAL'].includes(error.code)) server.emit('error', error); }
    });
    return server;
  }
  const handlers = server.listeners('connection');
  server.removeAllListeners('connection');
  const secret = crypto.randomBytes(32);
  server.on('connection', socket => {
    const nonce = crypto.randomBytes(32);
    let buffer = Buffer.alloc(0);
    socket.on('error', () => {});
    socket.setTimeout(5000, () => socket.destroy());
    socket.write(nonce);
    function authenticate(chunk) {
      buffer = Buffer.concat([buffer, chunk]);
      if (buffer.length < 32) return;
      if (!crypto.timingSafeEqual(buffer.subarray(0, 32), proof(secret, 'client', nonce))) return socket.destroy();
      socket.removeListener('data', authenticate);
      socket.setTimeout(0);
      socket.write(proof(secret, 'server', nonce));
      handlers.forEach(handler => handler.call(server, socket));
      if (buffer.length > 32) socket.emit('data', buffer.subarray(32));
    }
    socket.on('data', authenticate);
  });
  server.listen({host: '127.0.0.1', port: 0}, () => {
    try {
      metadata('publish', logical, {schema:'portal.local-ipc.v1', host:'127.0.0.1',
        port:server.address().port, secret:secret.toString('hex')}, python);
    } catch (error) { server.close(); server.emit('error', error); }
  });
  server.once('close', () => metadata('remove', logical, {secret:secret.toString('hex')}, python));
  return server;
}

function connectLocal(logical, ready, {python, tcp = defaultTCP(logical)} = {}) {
  if (!tcp) {
    const socket = net.createConnection(logical, () => ready(socket));
    return socket;
  }
  const record = metadata('read', logical, undefined, python);
  const secret = Buffer.from(record.secret, 'hex');
  const socket = net.createConnection({host:'127.0.0.1', port:record.port});
  let nonce, buffer = Buffer.alloc(0);
  socket.setTimeout(5000, () => socket.destroy(new Error('Local IPC authentication timed out')));
  function authenticate(chunk) {
    buffer = Buffer.concat([buffer, chunk]);
    if (!nonce && buffer.length >= 32) {
      nonce = buffer.subarray(0,32); buffer = buffer.subarray(32);
      socket.write(proof(secret, 'client', nonce));
    }
    if (!nonce || buffer.length < 32) return;
    if (!crypto.timingSafeEqual(buffer.subarray(0,32), proof(secret, 'server', nonce))) {
      socket.destroy(new Error('Local IPC server authentication failed')); return;
    }
    socket.removeListener('data', authenticate);
    socket.setTimeout(0);
    ready(socket);
    if (buffer.length > 32) socket.emit('data', buffer.subarray(32));
  }
  socket.on('data', authenticate);
  return socket;
}
module.exports = {listenLocal, connectLocal};
