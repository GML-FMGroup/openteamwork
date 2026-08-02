/** A structured non-success response returned by the OpenPPX Client API. */
export class ClientApiRequestError extends Error {
  public constructor(
    message: string,
    public readonly status: number,
    public readonly code: string,
  ) {
    super(message);
    this.name = "ClientApiRequestError";
  }
}
