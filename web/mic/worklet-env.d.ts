// Globals available inside an AudioWorkletGlobalScope (not part of lib.dom).
declare abstract class AudioWorkletProcessor {
  readonly port: MessagePort;
  constructor(options?: unknown);
  abstract process(
    inputs: Float32Array[][],
    outputs: Float32Array[][],
    parameters: Record<string, Float32Array>,
  ): boolean;
}

declare function registerProcessor(name: string, processorCtor: new (options?: unknown) => AudioWorkletProcessor): void;

declare const sampleRate: number;
declare const currentTime: number;
