const fs = require('node:fs');
const {connectLocal} = require(process.env.PORTAL_LOCAL_IPC_MODULE);
exports.inputs = () => JSON.parse(fs.readFileSync(process.env.PORTAL_SCRIPT_INPUT, 'utf8'));
exports.setOutput = fields => {
  if (!fields || Array.isArray(fields) || typeof fields !== 'object') throw new Error('输出必须是 JSON 对象');
  const data = JSON.stringify(fields);
  if (Buffer.byteLength(data) > 65536) throw new Error('输出不能超过 64 KB');
  fs.writeFileSync(process.env.PORTAL_SCRIPT_OUTPUT, data);
};
exports.isa = (instruction, args = {}) => new Promise((resolve, reject) => {
  const chunks = []; let size = 0;
  let completed = false;
  const client = connectLocal(process.env.PORTAL_ISA_SOCKET, authenticated => {
    authenticated.setTimeout(360000, () => authenticated.destroy(new Error('ISA 请求超时')));
    authenticated.on('data', chunk => {
    chunks.push(chunk); size += chunk.length;
    if (size > 8 * 1024 * 1024) client.destroy(new Error('ISA 结果过大'));
    if (chunk.includes(10)) {
      completed = true;
      client.end();
      try {
        const value = JSON.parse(Buffer.concat(chunks).toString());
        if (value.status !== 'committed') reject(new Error(JSON.stringify(value.error)));
        else resolve(value.result);
      } catch (error) { reject(error); }
    }
    });
    authenticated.write(JSON.stringify({instruction, arguments: args}) + '\n');
  });
  client.on('error', reject);
  client.on('close', () => { if (!completed) reject(new Error('ISA connection closed before a response')); });
});
