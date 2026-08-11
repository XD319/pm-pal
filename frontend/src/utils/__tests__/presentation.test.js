import { describe, expect, it } from 'vitest';
import { formatSourceType, formatStatus, formatToastType } from '../presentation';

describe('presentation helpers', () => {
  it('converts backend statuses to user-facing Chinese labels', () => {
    expect(formatStatus('ready_for_delivery')).toBe('可交付');
    expect(formatStatus('pending_approval')).toBe('待审批');
    expect(formatStatus('awaiting_confirmation')).toBe('待确认');
    expect(formatStatus('quality_checked')).toBe('已质检');
    expect(formatStatus('unknown_status')).toBe('unknown_status');
  });

  it('converts source and notification metadata', () => {
    expect(formatSourceType('feishu_doc')).toBe('飞书文档');
    expect(formatSourceType('feishu')).toBe('飞书文档');
    expect(formatSourceType('upload')).toBe('上传文件');
    expect(formatToastType('error')).toBe('操作失败');
  });
});
