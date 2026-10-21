import { useCallback, useEffect, useState } from 'react';
import { readWorkflowPage, workflowUrl, type WorkflowPage } from './services/workflow';

export function useWorkflowNavigation(hasAnalysis: boolean) {
  const [requestedPage, setPage] = useState<WorkflowPage>(() => readWorkflowPage(window.location.search, hasAnalysis));
  const page = !hasAnalysis && (requestedPage === 'clips' || requestedPage === 'studio') ? 'source' : requestedPage;

  const navigate = useCallback((next: WorkflowPage, replace = false) => {
    const url = workflowUrl(window.location.href, next);
    if (url !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
      window.history[replace ? 'replaceState' : 'pushState'](window.history.state, '', url);
    }
    setPage(next);
  }, []);

  useEffect(() => {
    const restore = () => navigate(readWorkflowPage(window.location.search, hasAnalysis), true);
    window.addEventListener('popstate', restore);
    return () => window.removeEventListener('popstate', restore);
  }, [hasAnalysis, navigate]);

  useEffect(() => {
    const url = workflowUrl(window.location.href, page);
    window.history.replaceState(window.history.state, '', url);
  }, [page]);

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: 'instant' });
    document.getElementById('workflow-page-title')?.focus({ preventScroll: true });
  }, [page]);

  return { page, navigate };
}
