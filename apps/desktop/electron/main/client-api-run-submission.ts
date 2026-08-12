/** Submit one non-idempotent Run, recovering only when the first request was not delivered. */
export async function submitClientApiRunWithRecovery<T>(options: {
  submit: () => Promise<T>;
  recoverAfterUndeliveredRequest: (error: unknown) => Promise<boolean>;
}): Promise<T> {
  try {
    return await options.submit();
  } catch (error) {
    if (!(await options.recoverAfterUndeliveredRequest(error))) {
      throw error;
    }
  }
  return options.submit();
}
