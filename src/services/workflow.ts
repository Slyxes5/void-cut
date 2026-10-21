export type WorkflowPage = 'source' | 'clips' | 'studio' | 'exports';

export function readWorkflowPage(search: string, hasAnalysis: boolean): WorkflowPage {
  const page = new URLSearchParams(search).get('view');
  if (page === 'exports') return page;
  if (hasAnalysis && (page === 'clips' || page === 'studio')) return page;
  return 'source';
}

/** Preserve private handoff fragments and unrelated URL parameters. */
export function workflowUrl(href: string, page: WorkflowPage): string {
  const url = new URL(href);
  url.searchParams.set('view', page);
  return `${url.pathname}${url.search}${url.hash}`;
}
