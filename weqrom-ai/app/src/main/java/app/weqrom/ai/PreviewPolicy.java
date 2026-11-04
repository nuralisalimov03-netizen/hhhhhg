package app.weqrom.ai;

/** A response-header policy cannot be loosened by model-generated HTML. */
public final class PreviewPolicy {
    private PreviewPolicy() { }
    public static final String CSP = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        + "img-src data: blob:; media-src data: blob:; connect-src 'none'; frame-src 'none'; "
        + "object-src 'none'; form-action 'none'; base-uri 'none'; worker-src 'none'";
}
