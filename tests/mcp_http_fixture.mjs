// Protocol fixture only: ephemeral loopback server, no production data or identity.
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { createMcpHandler } from '../integrations/mcp/sites.mjs';
const { tools } = JSON.parse(await readFile(new URL('../integrations/mcp/tools.json', import.meta.url)));
const items = new Map();
const handler = createMcpHandler({ tools, version: 'interop-test', authorize: () => {}, dispatch: async request => {
  const path = new URL(request.url).pathname;
  if (request.method === 'POST' && path === '/api/items') {
    const item = { ...await request.json(), id: crypto.randomUUID(), version: 1 };
    items.set(item.id, item); return Response.json({ item });
  }
  if (request.method === 'GET' && path.startsWith('/api/items/')) {
    const item = items.get(path.split('/').at(-1));
    return item ? Response.json({ item }) : Response.json({ error: 'Missing' }, { status: 404 });
  }
  return Response.json({ error: 'Unavailable fixture route' }, { status: 404 });
} });
const server = createServer(async (incoming, outgoing) => {
  try {
    const chunks = []; for await (const chunk of incoming) chunks.push(chunk);
    const request = new Request(`http://127.0.0.1:${server.address().port}${incoming.url}`, {
      method: incoming.method, headers: incoming.headers,
      ...(['GET', 'HEAD'].includes(incoming.method) ? {} : { body: Buffer.concat(chunks) })
    });
    const response = await handler(request, {});
    outgoing.writeHead(response.status, Object.fromEntries(response.headers));
    outgoing.end(Buffer.from(await response.arrayBuffer()));
  } catch { outgoing.writeHead(500); outgoing.end(); }
});
server.listen(0, '127.0.0.1', () => process.stdout.write(String(server.address().port) + '\n'));
process.on('SIGTERM', () => server.close(() => process.exit(0)));
