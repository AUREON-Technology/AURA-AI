/**
 * Pass a command's output through up to `head` characters, then keep only the last `tail`:
 * a build's cause is at the top and its verdict at the bottom. Memory stays bounded however
 * much a command prints, and a push costs the same however full the tail is.
 */
export class CappedOutput {
  private headLeft: number;
  /** The newest chunks; entries before `start` are already dropped. */
  private chunks: string[] = [];
  private start = 0;
  private held = 0;
  private dropped = 0;

  constructor(
    head: number,
    private readonly tailSize: number,
    private readonly emit: (chunk: string) => void,
  ) {
    this.headLeft = head;
  }

  push(chunk: string): void {
    if (this.headLeft > 0) {
      const part = chunk.slice(0, this.headLeft);
      this.headLeft -= part.length;
      if (part) this.emit(part);
      chunk = chunk.slice(part.length);
    }
    if (!chunk) return;
    if (chunk.length > this.tailSize) {
      this.dropped += chunk.length - this.tailSize;
      chunk = chunk.slice(chunk.length - this.tailSize);
    }
    this.chunks.push(chunk);
    this.held += chunk.length;
    // Drop whole chunks while the rest still fills the tail; `finish` trims the remainder.
    while (this.held - this.chunks[this.start]!.length >= this.tailSize) {
      const length = this.chunks[this.start]!.length;
      this.held -= length;
      this.dropped += length;
      this.start += 1;
    }
    if (this.start > 4096 && this.start * 2 > this.chunks.length) {
      this.chunks = this.chunks.slice(this.start);
      this.start = 0;
    }
  }

  /** Flush the tail. True when something in the middle was dropped. */
  finish(): boolean {
    let tail = this.chunks.slice(this.start).join("");
    const excess = tail.length - this.tailSize;
    if (excess > 0) {
      this.dropped += excess;
      tail = tail.slice(excess);
    }
    if (this.dropped > 0) this.emit(`\n[aura] ${this.dropped} characters omitted from the middle\n`);
    if (tail) this.emit(tail);
    this.chunks = [];
    this.start = 0;
    this.held = 0;
    return this.dropped > 0;
  }
}
