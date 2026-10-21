declare module '*unisoc-ota/errors.js' {
    export function explainError(value: unknown, english?: boolean): {message:string;details:string};
    export function isConnectionError(value: unknown): boolean;
}
