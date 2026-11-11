import { describe, expect, it } from 'vitest';
import { buildToolActivities, summarizeToolCall } from './toolActivity';
import type { IntermediateData } from '../types';

describe('toolActivity', () => {
  it('summarizes common calls into a short human-readable line', () => {
    expect(summarizeToolCall('http_request', '{"url":"https://id.redhaze.top/home","method":"GET"}'))
      .toBe('GET https://id.redhaze.top/home');
    expect(summarizeToolCall('dns_lookup', { domain: 'id.redhaze.top', record_type: 'A' }))
      .toBe('id.redhaze.top A');
  });

  it('pairs call and output by call_id', () => {
    const items: IntermediateData[] = [
      {
        itemType: 'function_call',
        label: 'HTTP 请求',
        detail: '{"url":"https://target/home","method":"GET"}',
        toolName: 'http_request',
        callId: 'call_1',
      },
      {
        itemType: 'function_call_output',
        label: 'HTTP 请求 输出',
        detail: 'HTTP 200\n<html>ok</html>',
        toolName: 'http_request',
        callId: 'call_1',
        duration: 0.23,
      },
    ];
    const [activity] = buildToolActivities(items);
    expect(activity.toolName).toBe('http_request');
    expect(activity.summary).toBe('GET https://target/home');
    expect(activity.hasOutput).toBe(true);
    expect(activity.status).toBe('ok');
    expect(activity.duration).toBe(0.23);
  });

  it('marks failed and incomplete calls clearly', () => {
    const failed = buildToolActivities([
      { itemType: 'function_call', label: '目录枚举', detail: '{}', toolName: 'gobuster_enum', callId: 'c1' },
      { itemType: 'function_call_output', label: '输出', detail: '[gobuster 执行失败 exit=1] wordlist missing', toolName: 'gobuster_enum', callId: 'c1' },
    ])[0];
    expect(failed.status).toBe('error');

    const pending = buildToolActivities([
      { itemType: 'function_call', label: '端口扫描', detail: '{}', toolName: 'port_scan', callId: 'c2' },
    ], false)[0];
    expect(pending.status).toBe('pending');
  });

  it('does not treat a normal HTTP 404 response as a tool failure', () => {
    const activity = buildToolActivities([
      { itemType: 'function_call', label: 'HTTP 请求', detail: '{"url":"https://target/missing"}', toolName: 'http_request', callId: 'c3' },
      { itemType: 'function_call_output', label: '输出', detail: 'HTTP 404\n404 page not found', toolName: 'http_request', callId: 'c3' },
    ])[0];
    expect(activity.status).toBe('ok');
  });
});
