package app.weqrom;

import android.animation.Animator;
import android.animation.AnimatorListenerAdapter;
import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PathMeasure;
import android.graphics.RadialGradient;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.Shader;
import android.graphics.Typeface;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowInsets;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.animation.LinearInterpolator;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

/** A self-contained native handwritten welcome, with no font-shaped greeting. */
public final class NativeHelloView extends View {
    private static final String[] WORDS = {"Привет", "Прывітанне", "Hello", "Bonjour", "Hola", "Ciao", "Hallo", "Сәлем", "Merhaba"};
    private static final Map<Character, Glyph> GLYPHS = createGlyphs();
    private static final ArrayList<InkWord> INK = createWords();
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Paint backgroundPaint = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.FILTER_BITMAP_FLAG);
    private final Paint inkPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint captionPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint pillPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Rect destination = new Rect();
    private final RectF pill = new RectF();
    private final Path partial = new Path();
    private Bitmap backdrop;
    private ValueAnimator animator;
    private Runnable onContinue;
    private String language = "en";
    private String caption = "Tap to continue";
    private int wordIndex = 2;
    private int phase;
    private int insetBottom;
    private float fraction;
    private float wordAlpha = 1f;
    private float downX;
    private float downY;
    private boolean attached;
    private boolean paused;
    private boolean disposed;
    private boolean reducedMotion;
    private boolean continued;
    private boolean touching;
    private final Runnable advance = new Runnable() {
        public void run() {
            if (!mayRun()) return;
            if (motionReduced()) { showStill(); return; }
            phase = 2;
            animateFade();
        }
    };

    public NativeHelloView(Context context) {
        super(context);
        inkPaint.setColor(Color.WHITE);
        inkPaint.setStyle(Paint.Style.STROKE);
        inkPaint.setStrokeCap(Paint.Cap.ROUND);
        inkPaint.setStrokeJoin(Paint.Join.ROUND);
        captionPaint.setColor(0xf5ffffff);
        captionPaint.setTextAlign(Paint.Align.CENTER);
        captionPaint.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        captionPaint.setTextSize(15f * getResources().getDisplayMetrics().scaledDensity);
        pillPaint.setColor(0xf0ffffff);
        setFocusable(true);
        setClickable(true);
        setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_YES);
        setLanguage(Locale.getDefault().getLanguage());
    }

    public void setOnContinueListener(Runnable listener) {
        onContinue = listener;
        continued = false;
    }

    public void setLanguage(String value) {
        language = "ru".equals(value) ? "ru" : "be".equals(value) ? "be" : "en";
        caption = "ru".equals(language) ? "Нажмите, чтобы продолжить" : "be".equals(language) ? "Націсніце, каб працягнуць" : "Tap to continue";
        setContentDescription("Weqrom. " + caption);
        stopAnimation();
        wordIndex = "ru".equals(language) ? 0 : "be".equals(language) ? 1 : 2;
        phase = 0;
        fraction = 0f;
        wordAlpha = 1f;
        if (motionReduced()) showStill(); else startIfVisible();
        invalidate();
    }

    public void setReducedMotion(boolean value) {
        reducedMotion = value;
        stopAnimation();
        if (motionReduced()) showStill(); else startIfVisible();
    }

    /** Keep drawing state while the host is in the background. */
    public void pause() {
        paused = true;
        stopAnimation();
    }

    public void resume() {
        if (disposed) return;
        paused = false;
        startIfVisible();
    }

    /** The host calls this when moving beyond the greeting page. */
    public void dispose() {
        if (disposed) return;
        disposed = true;
        paused = true;
        onContinue = null;
        stopAnimation();
        if (backdrop != null) { backdrop.recycle(); backdrop = null; }
    }

    @Override protected void onAttachedToWindow() {
        super.onAttachedToWindow();
        attached = true;
        startIfVisible();
    }

    @Override protected void onDetachedFromWindow() {
        attached = false;
        stopAnimation();
        super.onDetachedFromWindow();
    }

    @Override protected void onWindowVisibilityChanged(int visibility) {
        super.onWindowVisibilityChanged(visibility);
        if (handler == null) return;
        if (visibility == VISIBLE) startIfVisible(); else stopAnimation();
    }

    @Override protected void onVisibilityChanged(View changedView, int visibility) {
        super.onVisibilityChanged(changedView, visibility);
        if (handler == null) return;
        if (visibility == VISIBLE) startIfVisible(); else stopAnimation();
    }

    @Override public void onWindowFocusChanged(boolean hasWindowFocus) {
        super.onWindowFocusChanged(hasWindowFocus);
        if (hasWindowFocus) startIfVisible(); else stopAnimation();
    }

    @Override public WindowInsets onApplyWindowInsets(WindowInsets insets) {
        insetBottom = insets.getSystemWindowInsetBottom();
        invalidate();
        return super.onApplyWindowInsets(insets);
    }

    @Override protected void onSizeChanged(int width, int height, int oldWidth, int oldHeight) {
        super.onSizeChanged(width, height, oldWidth, oldHeight);
        destination.set(0, 0, width, height);
        if (width <= 0 || height <= 0 || disposed) return;
        if (backdrop != null) backdrop.recycle();
        // One static bitmap, at most 320 pixels across and 720 pixels tall.
        // Bilinear scaling gives a soft mesh without animated blur or pixel loops.
        int bitmapWidth = Math.min(width, 320);
        int bitmapHeight = Math.max(1, Math.min(720, Math.round((float) height * bitmapWidth / width)));
        backdrop = Bitmap.createBitmap(bitmapWidth, bitmapHeight, Bitmap.Config.ARGB_8888);
        Canvas canvas = new Canvas(backdrop);
        Paint mesh = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.DITHER_FLAG);
        mesh.setShader(new LinearGradient(0, 0, bitmapWidth * .52f, bitmapHeight,
            new int[] {0xff16acb5, 0xff178fac, 0xff7d96a1, 0xffd3a260},
            new float[] {0f, .32f, .67f, 1f}, Shader.TileMode.CLAMP));
        canvas.drawRect(0, 0, bitmapWidth, bitmapHeight, mesh);
        glow(canvas, mesh, bitmapWidth * .87f, bitmapHeight * .40f, bitmapHeight * .49f, 0xdb2375b0);
        glow(canvas, mesh, bitmapWidth * .07f, bitmapHeight * .70f, bitmapHeight * .42f, 0xccb7959c);
        glow(canvas, mesh, bitmapWidth * .13f, bitmapHeight * .13f, bitmapHeight * .46f, 0x9b148c92);
        glow(canvas, mesh, bitmapWidth * .94f, bitmapHeight * .94f, bitmapHeight * .37f, 0xbdddab55);
    }

    private static void glow(Canvas canvas, Paint paint, float x, float y, float radius, int color) {
        paint.setShader(new RadialGradient(x, y, radius, new int[] {color, color & 0x00ffffff},
            new float[] {0f, 1f}, Shader.TileMode.CLAMP));
        canvas.drawRect(0, 0, canvas.getWidth(), canvas.getHeight(), paint);
    }

    @Override protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        if (disposed) return;
        if (backdrop != null) canvas.drawBitmap(backdrop, null, destination, backgroundPaint);
        else canvas.drawColor(0xff258da6);
        InkWord word = INK.get(wordIndex);
        int safeBottom = bottomInset();
        float targetWidth = Math.min(getWidth() * .77f, dp(340));
        float scale = Math.min(targetWidth / Math.max(word.bounds.width(), 1f), dp(86) / Math.max(word.bounds.height(), 1f));
        if (scale > 0f) {
            int save = canvas.save();
            float centerY = Math.min(getHeight() * .465f, getHeight() - safeBottom - dp(165));
            canvas.translate(getWidth() * .5f, Math.max(dp(75), centerY));
            canvas.scale(scale, scale);
            canvas.translate(-word.bounds.centerX(), -word.bounds.centerY());
            inkPaint.setStrokeWidth(dp(2.65f) / scale);
            inkPaint.setAlpha(Math.round(255f * wordAlpha));
            float remaining = word.length * fraction;
            for (Stroke stroke : word.strokes) {
                if (remaining <= 0f) break;
                if (remaining >= stroke.length) canvas.drawPath(stroke.path, inkPaint);
                else {
                    partial.rewind();
                    stroke.measure.getSegment(0f, remaining, partial, true);
                    // A non-empty contour also draws correctly on older HW canvases.
                    partial.rLineTo(0f, 0f);
                    canvas.drawPath(partial, inkPaint);
                }
                remaining -= stroke.length;
            }
            canvas.restoreToCount(save);
        }
        float bottom = getHeight() - safeBottom;
        float captionY = bottom - dp(82);
        canvas.drawText(caption, getWidth() * .5f, captionY, captionPaint);
        float pillWidth = Math.min(dp(122), getWidth() * .32f);
        pill.set((getWidth() - pillWidth) * .5f, bottom - dp(24), (getWidth() + pillWidth) * .5f, bottom - dp(19));
        canvas.drawRoundRect(pill, dp(3), dp(3), pillPaint);
    }

    private boolean mayRun() {
        return !disposed && !paused && attached && getWindowVisibility() == VISIBLE && isShown() && hasWindowFocus();
    }

    private int bottomInset() {
        int result = Math.max(insetBottom, getPaddingBottom());
        // Root listeners may consume the dispatched insets before this child.
        WindowInsets rootInsets = getRootWindowInsets();
        if (rootInsets != null) result = Math.max(result, rootInsets.getSystemWindowInsetBottom());
        return result;
    }

    private boolean motionReduced() {
        if (reducedMotion) return true;
        try { return Settings.Global.getFloat(getContext().getContentResolver(), Settings.Global.ANIMATOR_DURATION_SCALE, 1f) <= 0f; }
        catch (RuntimeException unavailable) { return true; }
    }

    private void startIfVisible() {
        if (!mayRun() || animator != null) return;
        if (motionReduced()) { showStill(); return; }
        if (phase == 0) animateInk();
        else if (phase == 1) { handler.removeCallbacks(advance); handler.postDelayed(advance, 1150); }
        else animateFade();
    }

    private void animateInk() {
        if (!mayRun()) return;
        final ValueAnimator writing = ValueAnimator.ofFloat(fraction, 1f);
        animator = writing;
        float seconds = Math.max(2.35f, Math.min(3.65f, INK.get(wordIndex).length / 480f));
        writing.setDuration(Math.max(1, Math.round(seconds * 1000f * (1f - fraction))));
        writing.setInterpolator(new LinearInterpolator());
        writing.addUpdateListener(new ValueAnimator.AnimatorUpdateListener() {
            public void onAnimationUpdate(ValueAnimator value) {
                fraction = ((Float) value.getAnimatedValue()).floatValue();
                invalidate();
            }
        });
        writing.addListener(new AnimatorListenerAdapter() {
            @Override public void onAnimationEnd(Animator animation) {
                if (animator != writing) return;
                animator = null;
                fraction = 1f;
                phase = 1;
                if (mayRun()) handler.postDelayed(advance, 1150);
            }
        });
        writing.start();
    }

    private void animateFade() {
        if (!mayRun()) return;
        final ValueAnimator fading = ValueAnimator.ofFloat(wordAlpha, 0f);
        animator = fading;
        fading.setDuration(Math.max(1, Math.round(220f * wordAlpha)));
        fading.setInterpolator(new LinearInterpolator());
        fading.addUpdateListener(new ValueAnimator.AnimatorUpdateListener() {
            public void onAnimationUpdate(ValueAnimator value) {
                wordAlpha = ((Float) value.getAnimatedValue()).floatValue();
                invalidate();
            }
        });
        fading.addListener(new AnimatorListenerAdapter() {
            @Override public void onAnimationEnd(Animator animation) {
                if (animator != fading) return;
                animator = null;
                wordIndex = (wordIndex + 1) % INK.size();
                phase = 0;
                fraction = 0f;
                wordAlpha = 1f;
                if (mayRun()) startIfVisible();
            }
        });
        fading.start();
    }

    private void showStill() {
        stopAnimation();
        fraction = 1f;
        wordAlpha = 1f;
        phase = 1;
        invalidate();
    }

    private void stopAnimation() {
        if (handler != null) handler.removeCallbacksAndMessages(null);
        if (animator != null) {
            ValueAnimator previous = animator;
            animator = null;
            previous.removeAllUpdateListeners();
            previous.removeAllListeners();
            previous.cancel();
        }
    }

    @Override public boolean onTouchEvent(MotionEvent event) {
        if (disposed || !isEnabled()) return false;
        if (event.getActionMasked() == MotionEvent.ACTION_DOWN) {
            downX = event.getX(); downY = event.getY(); touching = true; return true;
        }
        if (event.getActionMasked() == MotionEvent.ACTION_CANCEL) { touching = false; return true; }
        if (event.getActionMasked() == MotionEvent.ACTION_UP && touching) {
            touching = false;
            float horizontal = Math.abs(event.getX() - downX);
            float vertical = downY - event.getY();
            boolean swipe = vertical >= dp(65) && vertical > horizontal * 1.2f;
            boolean tap = Math.abs(vertical) <= dp(18) && horizontal <= dp(18) && downY >= getHeight() - bottomInset() - dp(140);
            if (swipe || tap) return performClick();
            return true;
        }
        return touching;
    }

    @Override public boolean performClick() {
        super.performClick();
        if (!disposed && !continued && onContinue != null) {
            continued = true;
            pause();
            onContinue.run();
        }
        return true;
    }

    @Override public boolean onKeyUp(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_ENTER || keyCode == KeyEvent.KEYCODE_DPAD_CENTER || keyCode == KeyEvent.KEYCODE_SPACE) return performClick();
        return super.onKeyUp(keyCode, event);
    }

    @Override public void onInitializeAccessibilityNodeInfo(AccessibilityNodeInfo info) {
        super.onInitializeAccessibilityNodeInfo(info);
        info.setClassName("android.widget.Button");
        info.setText(caption);
        info.setClickable(true);
    }

    private float dp(float value) { return getResources().getDisplayMetrics().density * value; }

    private static final class Glyph {
        final float advance;
        final String[] strokes;
        Glyph(float advance, String[] strokes) { this.advance = advance; this.strokes = strokes; }
    }

    private static final class Stroke {
        final Path path;
        final PathMeasure measure;
        final float length;
        Stroke(Path path) { this.path = path; measure = new PathMeasure(path, false); length = measure.getLength(); }
    }

    private static final class InkWord {
        final ArrayList<Stroke> strokes = new ArrayList<Stroke>();
        final RectF bounds = new RectF();
        float length;
        InkWord(String text) {
            float pen = 0f;
            boolean first = true;
            for (int i = 0; i < text.length(); i++) {
                Glyph glyph = GLYPHS.get(Character.valueOf(text.charAt(i)));
                if (glyph == null) throw new IllegalStateException("Missing original stroke glyph: " + text.charAt(i));
                for (String data : glyph.strokes) {
                    Path path = parse(data);
                    path.offset(pen, 0f);
                    Stroke stroke = new Stroke(path);
                    strokes.add(stroke);
                    length += stroke.length;
                    RectF box = new RectF();
                    path.computeBounds(box, true);
                    if (first) { bounds.set(box); first = false; } else bounds.union(box);
                }
                pen += glyph.advance;
            }
        }
    }

    private static ArrayList<InkWord> createWords() {
        ArrayList<InkWord> words = new ArrayList<InkWord>();
        for (String text : WORDS) words.add(new InkWord(text));
        return words;
    }

    /** Original monoline glyphs. Each string is one pen-down stroke, in order. */
    private static Map<Character, Glyph> createGlyphs() {
        Map<Character, Glyph> glyphs = new LinkedHashMap<Character, Glyph>();
        add(glyphs, 'H', 52, "M 8 84 C 11 60 16 33 22 13", "M 49 12 C 42 36 36 62 33 84", "M 14 55 C 26 51 39 49 47 50");
        add(glyphs, 'e', 41, "M 4 65 C 14 66 33 52 30 44 C 26 35 11 40 7 52 C 1 68 7 85 20 85 C 27 85 34 80 40 74");
        add(glyphs, 'l', 30, "M 3 78 C 14 57 31 25 26 14 C 21 4 15 20 14 39 C 12 58 8 77 15 83 C 20 88 28 83 34 76");
        add(glyphs, 'o', 47, "M 34 43 C 17 34 6 46 5 64 C 4 83 17 91 29 81 C 41 71 42 46 31 42 C 28 40 29 51 37 52 C 42 53 45 51 48 48");
        add(glyphs, 'a', 49, "M 33 43 C 21 32 9 43 5 61 C 0 78 9 90 22 81 C 31 73 36 56 37 43", "M 37 43 C 34 59 26 80 33 84 C 38 87 46 81 50 76");
        add(glyphs, 'B', 54, "M 8 84 C 11 65 17 32 21 14", "M 21 14 C 35 4 54 11 49 27 C 45 40 28 48 16 49 C 34 42 53 47 48 65 C 43 81 22 88 8 84");
        add(glyphs, 'n', 47, "M 4 81 L 16 41", "M 11 62 C 22 41 39 34 38 48 L 29 76 C 26 88 37 87 47 76");
        add(glyphs, 'j', 27, "M 21 42 C 17 61 16 84 8 102 C 3 113 -8 113 -8 103 C -8 93 9 88 22 81", "M 25 23 L 25.2 23.2");
        add(glyphs, 'u', 45, "M 14 42 L 4 72 C -1 89 11 89 23 75 L 36 43", "M 36 43 L 27 74 C 22 88 32 87 45 77");
        add(glyphs, 'r', 40, "M 3 82 L 14 43", "M 12 54 C 22 37 35 39 34 50 C 33 55 28 59 25 60 C 30 56 37 57 42 62");
        add(glyphs, 'C', 57, "M 54 22 C 40 1 18 12 10 42 C -4 78 17 94 44 79");
        add(glyphs, 'i', 26, "M 15 44 L 5 73 C 1 87 13 89 24 77", "M 21 24 L 21.2 24.2");
        add(glyphs, 'M', 66, "M 3 85 L 25 14 L 33 59 L 66 13 L 49 85");
        add(glyphs, 'h', 49, "M 5 83 L 28 10 C 35 0 30 27 13 64 C 28 40 45 35 40 53 L 33 75 C 29 88 39 87 50 77");
        add(glyphs, 'b', 47, "M 4 82 C 13 58 25 25 25 14 C 24 0 14 16 13 40 L 7 70 C 3 85 14 91 25 80 C 37 69 42 45 31 42 C 18 38 14 58 28 59 C 35 60 43 54 47 49");
        add(glyphs, 'П', 66, "M 7 86 C 13 63 18 39 23 14", "M 23 14 C 40 10 57 11 67 14 C 61 37 52 63 47 84");
        add(glyphs, 'р', 43, "M 0 111 L 22 41", "M 16 59 C 29 30 47 37 40 60 C 34 84 15 92 10 79 C 17 87 30 85 44 75");
        add(glyphs, 'и', 45, "M 14 42 L 4 72 C -1 89 11 89 23 75 L 36 43", "M 36 43 L 27 74 C 22 88 32 87 45 77");
        add(glyphs, 'в', 45, "M 9 84 C 11 69 16 47 21 35", "M 21 35 C 37 20 49 31 42 43 C 39 50 28 55 17 57 C 34 51 47 56 40 71 C 35 83 19 87 9 84");
        add(glyphs, 'е', 41, "M 4 65 C 14 66 33 52 30 44 C 26 35 11 40 7 52 C 1 68 7 85 20 85 C 27 85 34 80 40 74");
        add(glyphs, 'т', 44, "M 4 44 C 18 41 32 41 45 43", "M 26 43 C 22 54 19 68 16 81 C 20 88 32 83 40 77");
        add(glyphs, 'ы', 51, "M 12 43 L 4 72 C 1 87 15 90 25 78 C 36 65 23 56 8 66", "M 41 43 L 33 75 C 30 89 40 87 50 77");
        add(glyphs, 'і', 26, "M 15 44 L 5 73 C 1 87 13 89 24 77", "M 21 24 L 21.2 24.2");
        add(glyphs, 'а', 49, "M 33 43 C 21 32 9 43 5 61 C 0 78 9 90 22 81 C 31 73 36 56 37 43", "M 37 43 C 34 59 26 80 33 84 C 38 87 46 81 50 76");
        add(glyphs, 'н', 44, "M 14 42 L 3 82", "M 37 42 L 25 82 C 31 87 39 81 44 77", "M 9 63 C 18 58 29 58 35 60");
        add(glyphs, 'С', 57, "M 54 22 C 40 1 18 12 10 42 C -4 78 17 94 44 79");
        add(glyphs, 'ә', 43, "M 10 46 C 21 34 40 39 39 57 C 39 75 28 87 15 84 C 4 81 4 70 12 65 C 22 64 32 66 40 61");
        add(glyphs, 'л', 49, "M 1 83 C 13 85 16 61 28 43 C 28 57 24 78 33 83 C 38 86 44 81 49 77");
        add(glyphs, 'м', 54, "M 4 83 L 17 44 L 24 71 L 49 43 L 39 82 C 44 86 50 81 54 77");
        return glyphs;
    }

    private static void add(Map<Character, Glyph> glyphs, char letter, float advance, String... strokes) {
        glyphs.put(Character.valueOf(letter), new Glyph(advance, strokes));
    }

    /** Tiny parser for our own absolute M/L/C paths; no fonts or hidden APIs. */
    private static Path parse(String data) {
        String[] tokens = data.split(" +");
        Path path = new Path();
        for (int i = 0; i < tokens.length;) {
            String command = tokens[i++];
            if ("M".equals(command) || "L".equals(command)) {
                float x = Float.parseFloat(tokens[i++]), y = Float.parseFloat(tokens[i++]);
                if ("M".equals(command)) path.moveTo(x, y); else path.lineTo(x, y);
            } else if ("C".equals(command)) {
                float a = Float.parseFloat(tokens[i++]), b = Float.parseFloat(tokens[i++]);
                float c = Float.parseFloat(tokens[i++]), d = Float.parseFloat(tokens[i++]);
                float e = Float.parseFloat(tokens[i++]), f = Float.parseFloat(tokens[i++]);
                path.cubicTo(a, b, c, d, e, f);
            } else throw new IllegalArgumentException("Unknown original stroke command: " + command);
        }
        return path;
    }
}
