const STATUS_LABELS = {
  pending: '待处理', proposed: '待评估', pending_approval: '待审批', approved: '已批准',
  rejected: '已拒绝', queued: '等待执行', running: '处理中', completed: '已完成',
  succeeded: '同步成功', failed: '执行失败', degraded: '部分完成', ready_for_delivery: '可交付',
  delivered: '已交付', pass: '质量通过', waived: '已豁免', draft: '草稿',
  awaiting_confirmation: '待确认', quality_checked: '已质检', dismissed: '已忽略',
  denied: '已拒绝', executable: '可执行', loading: '加载中',
};

export function formatStatus(value, fallback = '暂无状态') {
  const key = String(value || '').trim().toLowerCase();
  return STATUS_LABELS[key] || value || fallback;
}

export function formatSourceType(value) {
  const sourceTypes = {
    feishu: '飞书文档',
    feishu_doc: '飞书文档',
    prd_text: '粘贴正文',
    text: '粘贴正文',
    link: '链接资料',
    upload: '上传文件',
    local_file: '本地文件',
    url: '网页链接',
    github: 'GitHub',
    notion: 'Notion',
  };
  return sourceTypes[value] || value || '资料来源';
}
export function formatToastType(value) {
  return ({ success: '操作成功', error: '操作失败', warning: '请注意', info: '提示' })[value] || '提示';
}
