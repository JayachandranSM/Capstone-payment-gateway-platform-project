// hooks/useAsync.ts — generic hook for async operations with loading/error states.
import { useCallback, useState } from 'react';
import type { AsyncState } from '../types';

export function useAsync<T>() {
  const [state, setState] = useState<AsyncState<T>>({ status: 'idle' });

  const run = useCallback(async (promise: Promise<T>) => {
    setState({ status: 'loading' });
    try {
      const data = await promise;
      setState({ status: 'success', data });
      return data;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setState({ status: 'error', message });
      throw err;
    }
  }, []);

  const reset = useCallback(() => setState({ status: 'idle' }), []);

  return { state, run, reset };
}
