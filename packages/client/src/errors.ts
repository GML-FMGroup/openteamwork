/** A structured non-success response returned by the OpenPPX Client API. */
export class ClientApiRequestError extends Error {
  public constructor(
    message: string,
    public readonly status: number,
    public readonly code: string,
    public readonly details: Record<string, unknown> = {},
    public readonly retryable = false,
    public readonly requestId?: string,
    public readonly correlationId?: string,
  ) {
    super(message);
    this.name = "ClientApiRequestError";
  }
}
