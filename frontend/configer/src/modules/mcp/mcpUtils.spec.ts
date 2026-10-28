import { describe, expect, it } from 'vitest';
import {
  buildMcpPayload,
  mcpArgsToText,
  mcpBasicRows,
  mcpEditorBody,
  mcpHeadersToText,
  mcpListLine,
  mcpLogText,
  mcpServerId,
  mcpStatusColor,
  mcpStatusText,
  mcpTextToArgs,
  mcpTextToHeaders,
  mcpToolRows,
  normalizeTransport,
} from './mcpUtils';

describe('MCP transport form mapping', () => {
  it('treats anything other than sse/streamable-http as stdio', () => {
    expect(normalizeTransport('sse')).toBe('sse');
    expect(normalizeTransport('streamable-http')).toBe('streamable-http');
    expect(normalizeTransport('stdio')).toBe('stdio');
    expect(normalizeTransport('SSE')).toBe('stdio');
    expect(normalizeTransport(undefined)).toBe('stdio');
  });

  it('builds new-server defaults', () => {
    expect(mcpEditorBody(null)).toEqual({
      server_id: '',
      enabled: true,
      description: '',
      transport: 'stdio',
      custom: false,
      command: 'node',
      args: [],
      url: '',
      headers: {},
    });
    expect(mcpEditorBody(undefined).transport).toBe('stdio');
  });

  it('normalizes an existing record for the editor', () => {
    expect(mcpEditorBody({
      server_id: 'filesystem',
      enabled: false,
      description: 'fs',
      transport: 'streamable-http',
      custom: true,
      command: '',
      args: ['a', 2],
      url: 'https://example.com/mcp',
      headers: { Authorization: 'Bearer x', 'X-N': 1 },
    })).toEqual({
      server_id: 'filesystem',
      enabled: false,
      description: 'fs',
      transport: 'streamable-http',
      custom: true,
      command: 'node',
      args: ['a', '2'],
      url: 'https://example.com/mcp',
      headers: { Authorization: 'Bearer x', 'X-N': '1' },
    });
  });

  it('falls back to id and prefers server_id', () => {
    expect(mcpServerId({ server_id: 'a', id: 'b' })).toBe('a');
    expect(mcpServerId({ id: 'b' })).toBe('b');
    expect(mcpServerId(null)).toBe('');
  });
});

describe('MCP argument parsing', () => {
  it('joins args one per line', () => {
    expect(mcpArgsToText(['@modelcontextprotocol/server-filesystem', '/tmp']))
      .toBe('@modelcontextprotocol/server-filesystem\n/tmp');
    expect(mcpArgsToText('not-an-array')).toBe('');
    expect(mcpArgsToText(undefined)).toBe('');
  });

  it('splits args, trims lines and drops blanks (including CRLF)', () => {
    expect(mcpTextToArgs('  a  \r\n\r\n b \n   \n c ')).toEqual(['a', 'b', 'c']);
    expect(mcpTextToArgs('')).toEqual([]);
    expect(mcpTextToArgs(null)).toEqual([]);
  });
});

describe('MCP headers parsing', () => {
  it('serializes headers as key: value lines', () => {
    expect(mcpHeadersToText({ Authorization: 'Bearer x', 'X-N': 1 }))
      .toBe('Authorization: Bearer x\nX-N: 1');
    expect(mcpHeadersToText(null)).toBe('');
    expect(mcpHeadersToText('nope')).toBe('');
  });

  it('parses the first colon, trims both sides and skips invalid lines', () => {
    expect(mcpTextToHeaders(
      'Authorization: Bearer abc\r\n\r\n  X-Test :  v:1  \nno-colon\n:value\n',
    )).toEqual({
      Authorization: 'Bearer abc',
      'X-Test': 'v:1',
    });
  });
});

describe('MCP payload and status', () => {
  it('builds the payload exactly like the old save button', () => {
    expect(buildMcpPayload({
      server_id: '  filesystem ',
      enabled: 1 as unknown as boolean,
      description: ' fs ',
      transport: 'weird',
      custom: true,
      command: '  npx ',
      argsText: ' -y \n pkg \n',
      url: ' https://example.com/mcp ',
      headersText: 'Authorization: Bearer t',
    })).toEqual({
      server_id: 'filesystem',
      enabled: true,
      description: 'fs',
      transport: 'stdio',
      custom: true,
      command: 'npx',
      args: ['-y', 'pkg'],
      url: 'https://example.com/mcp',
      headers: { Authorization: 'Bearer t' },
    });
  });

  it('maps status text and dot colors', () => {
    expect(mcpStatusText({ status: 'running' })).toBe('运行中');
    expect(mcpStatusText({ status: 'error' })).toBe('错误');
    expect(mcpStatusText({ status: 'stopped' })).toBe('已停止');
    expect(mcpStatusColor({ status: 'running' })).toBe('#4caf50');
    expect(mcpStatusColor({ status: 'error' })).toBe('#f44336');
    expect(mcpStatusColor({})).toBe('#8b95a7');
  });

  it('renders the list line and detail rows', () => {
    expect(mcpListLine({ server_id: 'fs', transport: 'sse', status: 'running', tool_count: 3 }))
      .toBe('fs | sse | 运行中 | tools=3');
    expect(mcpListLine({ server_id: 'fs' })).toBe('fs | stdio | 已停止 | tools=0');

    const rows = mcpBasicRows({ server_id: 'fs', headers: { A: '1' }, command: '' });
    expect(rows.map((row) => row.label)).toEqual([
      '服务器标识', '状态', '启用', '传输', '自定义', '命令', 'URL', '请求头', '描述', '错误',
    ]);
    expect(rows[5].value).toBe('builtin');
    expect(rows[7].value).toBe('{"A":"1"}');
  });

  it('maps tool rows and log text', () => {
    expect(mcpToolRows({ tools: [{ name: 'read', description: '读文件' }] }))
      .toEqual([['read', '-', '读文件']]);
    expect(mcpToolRows({})).toEqual([]);
    expect(mcpLogText(['a', 'b'])).toBe('a\nb');
    expect(mcpLogText(undefined)).toBe('（无日志）');
  });
});
