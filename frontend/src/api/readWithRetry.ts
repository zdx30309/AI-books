/** One retry for transient, read-only requests; never use for paid generation. */
export async function readWithTransientRetry<T>(read: () => Promise<T>): Promise<T> {
  try {
    return await read();
  } catch (error) {
    if (error && typeof error === "object" && "status" in error
      && typeof error.status === "number" && error.status < 500) throw error;
    return read();
  }
}
