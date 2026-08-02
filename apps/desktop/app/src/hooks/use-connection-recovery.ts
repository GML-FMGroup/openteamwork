import { useEffect, useRef } from "react";

interface ConnectionRecoveryOptions {
  active: boolean;
  check: () => Promise<boolean>;
  onUnavailable: () => void;
  onRecovered: () => void | Promise<void>;
  intervalMs?: number;
}

/** Poll a configured Node without overlapping probes and report connection transitions. */
export function useConnectionRecovery(options: ConnectionRecoveryOptions): void {
  const callbacksRef = useRef(options);
  callbacksRef.current = options;

  useEffect(() => {
    if (!options.active) {
      return;
    }
    let disposed = false;
    let probing = false;
    let available = true;
    const probe = async (): Promise<void> => {
      if (probing) {
        return;
      }
      probing = true;
      let nextAvailable = false;
      try {
        nextAvailable = await callbacksRef.current.check();
      } catch {
        nextAvailable = false;
      } finally {
        probing = false;
      }
      if (disposed || nextAvailable === available) {
        return;
      }
      available = nextAvailable;
      if (available) {
        try {
          await callbacksRef.current.onRecovered();
        } catch {
          available = false;
          callbacksRef.current.onUnavailable();
        }
      } else {
        callbacksRef.current.onUnavailable();
      }
    };
    const timer = window.setInterval(() => void probe(), options.intervalMs ?? 5_000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [options.active, options.intervalMs]);
}
