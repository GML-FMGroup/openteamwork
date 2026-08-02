import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatMessage } from "../types";

/** Keep transcript auto-follow behavior independent from workspace data loading. */
export function useTranscriptFollow(messages: ChatMessage[], resetKey: number) {
  const streamRef = useRef<HTMLElement | null>(null);
  const nextScrollBehaviorRef = useRef<ScrollBehavior>("auto");
  const previousResetKeyRef = useRef(resetKey);
  const [followingLatest, setFollowingLatest] = useState(true);

  useEffect(() => {
    const stream = streamRef.current;
    if (!stream) {
      return;
    }
    const resetRequested = previousResetKeyRef.current !== resetKey;
    previousResetKeyRef.current = resetKey;
    const behavior = resetRequested ? "auto" : nextScrollBehaviorRef.current;
    nextScrollBehaviorRef.current = "smooth";
    if (resetRequested) {
      setFollowingLatest(true);
    } else if (!followingLatest && behavior !== "auto") {
      return;
    }
    if (typeof stream.scrollTo === "function") {
      stream.scrollTo({ top: stream.scrollHeight, behavior });
    } else {
      stream.scrollTop = stream.scrollHeight;
    }
  }, [followingLatest, messages, resetKey]);

  const followLatest = useCallback(() => setFollowingLatest(true), []);

  const handleScroll = useCallback(() => {
    const stream = streamRef.current;
    if (!stream) {
      return;
    }
    const distanceFromBottom = stream.scrollHeight - stream.scrollTop - stream.clientHeight;
    setFollowingLatest(distanceFromBottom < 72);
  }, []);

  const jumpToLatest = useCallback(() => {
    const stream = streamRef.current;
    setFollowingLatest(true);
    stream?.scrollTo({ top: stream.scrollHeight, behavior: "smooth" });
  }, []);

  return {
    streamRef,
    followingLatest,
    followLatest,
    handleScroll,
    jumpToLatest,
  };
}
