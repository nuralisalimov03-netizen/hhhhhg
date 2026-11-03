package app.weqrom;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.Application;
import android.app.Dialog;
import android.content.Context;
import android.content.DialogInterface;
import android.content.SharedPreferences;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.view.Gravity;
import android.view.HapticFeedbackConstants;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowManager;
import android.view.animation.DecelerateInterpolator;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;
import java.lang.ref.WeakReference;
import java.util.Locale;

/** Native, dependency-free UI. TikTok hooks are supplied through Integration. */
public final class WeqromUi {
    public static final String PREFERENCES = "weqrom";
    public static final String DEFAULT_REGION = "BY";
    private static final int ACCENT = 0xff9bff75;
    private static final String[] LANGUAGE_CODES = {"ru", "be", "en"};
    private static final String[] LANGUAGE_NAMES = {"Русский", "Беларуская", "English"};
    private static final String[] GREETINGS = {"Привет", "Прывітанне", "Hello", "Bonjour", "Hola", "Ciao", "Hallo", "Сәлем", "Merhaba", "你好", "こんにちは", "안녕하세요", "مرحبا"};
    private static final String[] REGION_CODES = {"BY", "KZ", "US", "GB", "DE", "FR", "PL", "TR", "UA", "AM", "GE", "AZ", "UZ", "LT", "LV", "EE", "CA", "JP", "KR"};
    private static final String[] REGION_NAMES_RU = {"Беларусь", "Казахстан", "США", "Великобритания", "Германия", "Франция", "Польша", "Турция", "Украина", "Армения", "Грузия", "Азербайджан", "Узбекистан", "Литва", "Латвия", "Эстония", "Канада", "Япония", "Южная Корея"};
    private static final String[] REGION_NAMES_EN = {"Belarus", "Kazakhstan", "United States", "United Kingdom", "Germany", "France", "Poland", "Türkiye", "Ukraine", "Armenia", "Georgia", "Azerbaijan", "Uzbekistan", "Lithuania", "Latvia", "Estonia", "Canada", "Japan", "South Korea"};
    private static final int[] LIKE_COLORS = {0xffff385d, 0xff3287ff, 0xff9bff75, 0xffa579ff, 0xffffaa40, 0xffef6fce};
    private static volatile Integration integration;
    private static WeakReference<Panel> active = new WeakReference<Panel>(null);

    private WeqromUi() {}

    /** A bridge reports only hooks actually wired to the original client. */
    public interface Integration {
        void apply(Activity activity, Options options);
        boolean hasRegionHook();
        boolean hasThemeHook();
        boolean hasLikeColorHook();
        void openAdvanced(Activity activity);
    }

    public static final class Options {
        public final String region;
        public final String theme;
        public final int likeColor;
        public final String language;
        public final boolean reducedMotion;
        public final boolean haptics;

        private Options(SharedPreferences p) {
            region = p.getString("region", DEFAULT_REGION);
            theme = p.getString("theme", "dark");
            likeColor = p.getInt("like_color", LIKE_COLORS[0]);
            language = p.getString("language", "ru");
            reducedMotion = p.getBoolean("reduced_motion", false);
            haptics = p.getBoolean("haptics", false);
        }
    }

    public static void setIntegration(Integration value) { integration = value; }

    /** Call before loading region hooks. Existing user choices are preserved. */
    public static void initialize(Context context) {
        SharedPreferences p = preferences(context);
        SharedPreferences.Editor e = p.edit();
        if (!p.contains("region")) e.putString("region", DEFAULT_REGION);
        if (!p.contains("theme")) e.putString("theme", "dark");
        if (!p.contains("like_color")) e.putInt("like_color", LIKE_COLORS[0]);
        if (!p.contains("language")) {
            String device = Locale.getDefault().getLanguage();
            e.putString("language", "be".equals(device) ? "be" : "ru".equals(device) ? "ru" : "en");
        }
        e.apply();
    }

    public static SharedPreferences preferences(Context context) {
        return context.getApplicationContext().getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE);
    }

    public static Options options(Context context) {
        initialize(context);
        return new Options(preferences(context));
    }

    public static String region(Context context) { return options(context).region; }
    public static String theme(Context context) { return options(context).theme; }
    public static int likeColor(Context context) { return options(context).likeColor; }
    public static String language(Context context) { return options(context).language; }
    public static boolean isWelcomeComplete(Context context) { return preferences(context).getBoolean("welcome_complete", false); }

    public static void showWelcome(Activity activity) { showWelcome(activity, null); }

    /** The callback resumes the host's normal feed; no account or network work. */
    public static void showWelcome(final Activity activity, final Runnable continueToFeed) {
        if (activity == null || activity.isFinishing() || activity.isDestroyed()) return;
        if (Looper.myLooper() != Looper.getMainLooper()) {
            activity.runOnUiThread(new Runnable() { public void run() { showWelcome(activity, continueToFeed); } });
            return;
        }
        initialize(activity);
        if (isWelcomeComplete(activity)) {
            if (continueToFeed != null) continueToFeed.run();
            return;
        }
        show(activity, false, continueToFeed);
    }

    public static void showSettings(final Activity activity) {
        if (activity == null || activity.isFinishing() || activity.isDestroyed()) return;
        if (Looper.myLooper() != Looper.getMainLooper()) {
            activity.runOnUiThread(new Runnable() { public void run() { showSettings(activity); } });
            return;
        }
        initialize(activity);
        show(activity, true, null);
    }

    private static void show(Activity activity, boolean settings, Runnable continueToFeed) {
        Panel previous = active.get();
        if (previous != null && !previous.closed) {
            if (previous.activity == activity && previous.settings == settings) {
                previous.resume();
                return;
            }
            previous.close();
        }
        Panel panel = new Panel(activity, settings, continueToFeed);
        active = new WeakReference<Panel>(panel);
        panel.open();
    }

    private static final class Panel implements Application.ActivityLifecycleCallbacks {
        final Activity activity;
        final boolean settings;
        final Runnable continueToFeed;
        final SharedPreferences prefs;
        final Handler handler = new Handler(Looper.getMainLooper());
        final Dialog dialog;
        AlertDialog pickerDialog;
        LinearLayout root;
        LinearLayout body;
        TextView greeting;
        int page;
        int greetingIndex;
        int background;
        int foreground;
        int secondary;
        int surface;
        int border;
        boolean closed;
        boolean hiddenForStop;
        boolean lifecycleRegistered;

        final Runnable greetingTick = new Runnable() {
            public void run() {
                if (closed || page != 0 || !dialog.isShowing()) return;
                if (animate()) {
                    greeting.animate().alpha(0f).translationY(-dp(7)).setDuration(170).withEndAction(new Runnable() {
                        public void run() {
                            if (closed || page != 0) return;
                            nextGreeting();
                            greeting.setTranslationY(dp(7));
                            greeting.animate().alpha(1f).translationY(0f).setDuration(280).setInterpolator(new DecelerateInterpolator()).start();
                        }
                    }).start();
                } else nextGreeting();
                handler.postDelayed(this, 2300);
            }
        };

        Panel(Activity activity, boolean settings, Runnable continueToFeed) {
            this.activity = activity;
            this.settings = settings;
            this.continueToFeed = continueToFeed;
            prefs = preferences(activity);
            dialog = new Dialog(activity, android.R.style.Theme_Material_Light_NoActionBar);
            dialog.setCancelable(settings);
            dialog.setCanceledOnTouchOutside(false);
            dialog.setOnDismissListener(new DialogInterface.OnDismissListener() {
                public void onDismiss(DialogInterface value) { cleanup(); }
            });
            page = settings ? 3 : 0;
        }

        void open() {
            activity.getApplication().registerActivityLifecycleCallbacks(this);
            lifecycleRegistered = true;
            refreshPalette();
            root = new LinearLayout(activity);
            root.setOrientation(LinearLayout.VERTICAL);
            root.setPadding(dp(24), dp(22), dp(24), dp(20));
            root.setBackgroundColor(background);
            root.setOnApplyWindowInsetsListener(new View.OnApplyWindowInsetsListener() {
                public android.view.WindowInsets onApplyWindowInsets(View view, android.view.WindowInsets insets) {
                    view.setPadding(dp(24) + insets.getSystemWindowInsetLeft(), dp(18) + insets.getSystemWindowInsetTop(),
                        dp(24) + insets.getSystemWindowInsetRight(), dp(16) + insets.getSystemWindowInsetBottom());
                    return insets;
                }
            });
            dialog.setContentView(root);
            dialog.show();
            Window window = dialog.getWindow();
            if (window != null) {
                window.setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT);
                window.setBackgroundDrawableResource(android.R.color.transparent);
                window.setStatusBarColor(background);
                window.setNavigationBarColor(background);
                int flags = View.SYSTEM_UI_FLAG_LAYOUT_STABLE | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN;
                if (!dark()) flags |= View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
                window.getDecorView().setSystemUiVisibility(flags);
                window.addFlags(WindowManager.LayoutParams.FLAG_DRAWS_SYSTEM_BAR_BACKGROUNDS);
            }
            render();
            root.requestApplyInsets();
        }

        void refreshPalette() {
            boolean dark = dark();
            background = dark ? 0xff080d0b : 0xfff7f9f6;
            foreground = dark ? 0xfff5faf3 : 0xff111c16;
            secondary = dark ? 0xff9ca99f : 0xff5d6c62;
            surface = dark ? 0xff151f19 : 0xffffffff;
            border = dark ? 0xff2c3c30 : 0xffdce5dd;
        }

        boolean dark() {
            String value = prefs.getString("theme", "dark");
            if ("system".equals(value)) return (activity.getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES;
            return !"light".equals(value);
        }

        String tr(String ru, String be, String en) {
            String language = prefs.getString("language", "en");
            return "ru".equals(language) ? ru : "be".equals(language) ? be : en;
        }

        void render() {
            handler.removeCallbacks(greetingTick);
            if (greeting != null) greeting.animate().cancel();
            root.removeAllViews();
            refreshPalette();
            root.setBackgroundColor(background);
            LinearLayout header = new LinearLayout(activity);
            header.setGravity(Gravity.CENTER_VERTICAL);
            TextView logo = text("W", 25, 0xff10200e, true);
            logo.setGravity(Gravity.CENTER);
            logo.setBackground(shape(ACCENT, 17, 0));
            header.addView(logo, new LinearLayout.LayoutParams(dp(48), dp(48)));
            TextView brand = text("Weqrom", 22, foreground, true);
            LinearLayout.LayoutParams brandParams = new LinearLayout.LayoutParams(0, dp(48), 1f);
            brandParams.leftMargin = dp(13);
            brand.setGravity(Gravity.CENTER_VERTICAL);
            header.addView(brand, brandParams);
            if (settings || page == 3) {
                TextView done = text(tr("Готово", "Гатова", "Done"), 15, foreground, true);
                done.setGravity(Gravity.CENTER);
                done.setMinWidth(dp(64));
                done.setMinHeight(dp(48));
                done.setOnClickListener(new View.OnClickListener() { public void onClick(View v) { haptic(v); if (settings) close(); else changePage(2); } });
                header.addView(done);
            } else {
                TextView progress = text((page + 1) + " / 3", 13, secondary, false);
                header.addView(progress);
            }
            root.addView(header, new LinearLayout.LayoutParams(-1, dp(52)));
            body = new LinearLayout(activity);
            body.setOrientation(LinearLayout.VERTICAL);
            LinearLayout.LayoutParams bodyParams = new LinearLayout.LayoutParams(-1, 0, 1f);
            bodyParams.topMargin = dp(18);
            root.addView(body, bodyParams);
            if (page == 0) greetingPage();
            else if (page == 1) languagePage();
            else if (page == 2) readyPage();
            else settingsPage();
        }

        void greetingPage() {
            LinearLayout center = new LinearLayout(activity);
            center.setOrientation(LinearLayout.VERTICAL);
            center.setGravity(Gravity.CENTER);
            body.addView(center, new LinearLayout.LayoutParams(-1, 0, 1f));
            TextView label = text("WELCOME / ВІТАЕМ", 11, secondary, true);
            label.setLetterSpacing(0.14f);
            center.addView(label);
            greeting = text(GREETINGS[greetingIndex], 48, foreground, true);
            greeting.setGravity(Gravity.CENTER);
            greeting.setMinHeight(dp(106));
            greeting.setMaxLines(1);
            greeting.setTextDirection(View.TEXT_DIRECTION_FIRST_STRONG);
            center.addView(greeting, new LinearLayout.LayoutParams(-1, dp(112)));
            TextView hint = text(tr("Ваш ритм. Ваша лента.", "Ваш рытм. Ваша стужка.", "Your pace. Your feed."), 17, secondary, false);
            hint.setGravity(Gravity.CENTER);
            center.addView(hint);
            Button next = primary(tr("Нажмите, чтобы продолжить", "Націсніце, каб працягнуць", "Tap to continue"));
            next.setOnClickListener(new View.OnClickListener() { public void onClick(View v) { haptic(v); changePage(1); } });
            body.addView(next, new LinearLayout.LayoutParams(-1, dp(58)));
            footnote(tr("Лёгкий интерфейс. Ничего лишнего.", "Лёгкі інтэрфейс. Нічога лішняга.", "A lighter interface. Room to breathe."));
            if (animate()) handler.postDelayed(greetingTick, 2100);
        }

        void nextGreeting() {
            greetingIndex = (greetingIndex + 1) % GREETINGS.length;
            greeting.setText(GREETINGS[greetingIndex]);
            greeting.setContentDescription(GREETINGS[greetingIndex]);
        }

        void languagePage() {
            heading(tr("Ваш язык", "Ваша мова", "Your language"),
                tr("Выберите язык панелей Weqrom.", "Выберыце мову панэляў Weqrom.", "Choose a language for Weqrom panels."));
            ScrollView scroll = new ScrollView(activity);
            scroll.setFillViewport(false);
            scroll.setClipToPadding(false);
            LinearLayout list = new LinearLayout(activity);
            list.setOrientation(LinearLayout.VERTICAL);
            scroll.addView(list);
            for (int i = 0; i < LANGUAGE_CODES.length; i++) {
                final String code = LANGUAGE_CODES[i];
                boolean selected = code.equals(prefs.getString("language", "en"));
                final TextView row = choice(LANGUAGE_NAMES[i], selected);
                row.setOnClickListener(new View.OnClickListener() {
                    public void onClick(View v) {
                        haptic(v);
                        prefs.edit().putString("language", code).apply();
                        apply();
                        render();
                    }
                });
                LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, dp(57));
                params.bottomMargin = dp(9);
                list.addView(row, params);
            }
            body.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1f));
            Button next = primary(tr("Продолжить", "Працягнуць", "Continue"));
            next.setOnClickListener(new View.OnClickListener() { public void onClick(View v) { haptic(v); changePage(2); } });
            LinearLayout.LayoutParams nextParams = new LinearLayout.LayoutParams(-1, dp(58));
            nextParams.topMargin = dp(12);
            body.addView(next, nextParams);
            footnote(tr("Язык панелей Weqrom.\nЯзык ленты меняется в настройках TikTok.",
                "Мова панэляў Weqrom.\nМова стужкі змяняецца ў наладах TikTok.",
                "Language for Weqrom panels.\nChange the feed language in TikTok settings."));
        }

        void readyPage() {
            LinearLayout center = new LinearLayout(activity);
            center.setOrientation(LinearLayout.VERTICAL);
            center.setGravity(Gravity.CENTER);
            body.addView(center, new LinearLayout.LayoutParams(-1, 0, 1f));
            TextView check = text("✓", 35, 0xff173215, true);
            check.setGravity(Gravity.CENTER);
            check.setBackground(shape(ACCENT, 40, 0));
            center.addView(check, new LinearLayout.LayoutParams(dp(80), dp(80)));
            TextView title = text(tr("Наслаждайтесь\nWeqrom", "Атрымлівайце асалоду\nад Weqrom", "Enjoy\nWeqrom"), 34, foreground, true);
            title.setGravity(Gravity.CENTER);
            LinearLayout.LayoutParams titleParams = new LinearLayout.LayoutParams(-1, -2);
            titleParams.topMargin = dp(25);
            center.addView(title, titleParams);
            TextView region = text(tr("Регион мода: ", "Рэгіён мода: ", "Mod region: ") + regionName(region(activity)), 15, secondary, false);
            region.setGravity(Gravity.CENTER);
            LinearLayout.LayoutParams regionParams = new LinearLayout.LayoutParams(-1, -2);
            regionParams.topMargin = dp(18);
            center.addView(region, regionParams);
            TextView edit = text(tr("Настроить перед стартом", "Наладзіць перад пачаткам", "Adjust before starting"), 15, foreground, true);
            edit.setGravity(Gravity.CENTER);
            edit.setMinHeight(dp(50));
            edit.setOnClickListener(new View.OnClickListener() { public void onClick(View v) { haptic(v); changePage(3); } });
            center.addView(edit, new LinearLayout.LayoutParams(-1, dp(52)));
            Button finish = primary(tr("В ленту рекомендаций", "У стужку рэкамендацый", "Open For You"));
            finish.setOnClickListener(new View.OnClickListener() {
                public void onClick(View v) {
                    haptic(v);
                    prefs.edit().putBoolean("welcome_complete", true).apply();
                    apply();
                    close();
                    if (continueToFeed != null && !activity.isFinishing() && !activity.isDestroyed()) continueToFeed.run();
                }
            });
            body.addView(finish, new LinearLayout.LayoutParams(-1, dp(58)));
            footnote(tr("Регион аккаунта и выдачу определяет также TikTok.", "Рэгіён акаўнта і выдачу вызначае таксама TikTok.", "TikTok also determines account region and recommendations."));
        }

        void settingsPage() {
            heading(tr("Под вас", "Пад вас", "Make it yours"),
                tr("Всё нужное — в одном месте.", "Усё патрэбнае — у адным месцы.", "The essentials, in one place."));
            ScrollView scroll = new ScrollView(activity);
            scroll.setClipToPadding(false);
            LinearLayout list = new LinearLayout(activity);
            list.setOrientation(LinearLayout.VERTICAL);
            scroll.addView(list);
            body.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1f));
            section(list, tr("РЕГИОН И ЯЗЫК", "РЭГІЁН І МОВА", "REGION & LANGUAGE"));
            addRow(list, tr("Регион мода", "Рэгіён мода", "Mod region"), regionName(region(activity)), new View.OnClickListener() { public void onClick(View v) { regionPicker(); } });
            note(list, regionHook() ? tr("Страна SIM и оператора, которую мод сообщает клиенту. Не переносит аккаунт и не меняет IP.", "Краіна SIM і аператара, якую мод паведамляе кліенту. Не пераносіць акаўнт і не мяняе IP.", "SIM and operator country reported to the client. Does not migrate your account or change your IP.")
                : tr("Выбор сохранён. Смена региона в этом клиенте недоступна.", "Выбар захаваны. Змена рэгіёна ў гэтым кліенце недаступная.", "Choice saved. Region changes are unavailable in this client."));
            addRow(list, tr("Язык Weqrom", "Мова Weqrom", "Weqrom language"), languageName(), new View.OnClickListener() { public void onClick(View v) { languagePicker(); } });
            section(list, tr("ОФОРМЛЕНИЕ", "АФАРМЛЕННЕ", "APPEARANCE"));
            String theme = prefs.getString("theme", "dark");
            String themeLabel = "light".equals(theme) ? tr("Светлое", "Светлае", "Light") : "system".equals(theme) ? tr("Как в системе", "Як у сістэме", "System") : tr("Тёмное", "Цёмнае", "Dark");
            addRow(list, tr("Тема", "Тэма", "Theme"), themeLabel, new View.OnClickListener() { public void onClick(View v) { themePicker(); } });
            if (!themeHook()) note(list, tr("Тема применяется к панелям Weqrom. Тема ленты управляется отдельно.", "Тэма ўжываецца да панэляў Weqrom. Тэма стужкі кіруецца асобна.", "Applies to Weqrom panels. Feed appearance is managed separately."));
            TextView colorsTitle = text(tr("Цвет акцента и лайков", "Колер акцэнту і лайкаў", "Accent and like color"), 16, foreground, true);
            LinearLayout.LayoutParams colorTitleParams = new LinearLayout.LayoutParams(-1, -2);
            colorTitleParams.topMargin = dp(18);
            list.addView(colorsTitle, colorTitleParams);
            LinearLayout colors = new LinearLayout(activity);
            colors.setGravity(Gravity.CENTER_VERTICAL);
            for (int i = 0; i < LIKE_COLORS.length; i++) {
                final int color = LIKE_COLORS[i];
                FrameLayout touch = new FrameLayout(activity);
                boolean selected = prefs.getInt("like_color", LIKE_COLORS[0]) == color;
                View dot = new View(activity);
                GradientDrawable background = shape(color, 30, 0);
                if (selected) background.setStroke(dp(3), foreground);
                dot.setBackground(background);
                FrameLayout.LayoutParams dotParams = new FrameLayout.LayoutParams(dp(36), dp(36), Gravity.CENTER);
                touch.addView(dot, dotParams);
                touch.setContentDescription(colorName(i) + (selected ? tr(", выбран", ", выбраны", ", selected") : ""));
                touch.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_YES);
                touch.setOnClickListener(new View.OnClickListener() {
                    public void onClick(View v) { haptic(v); prefs.edit().putInt("like_color", color).apply(); apply(); render(); }
                });
                colors.addView(touch, new LinearLayout.LayoutParams(0, dp(58), 1f));
            }
            list.addView(colors, new LinearLayout.LayoutParams(-1, dp(58)));
            if (!likeHook()) note(list, tr("Цвет сохранён. Изменение цвета в ленте недоступно.", "Колер захаваны. Змена колеру ў стужцы недаступная.", "Color saved. Changing feed colors is unavailable."));
            section(list, tr("КОМФОРТ", "КАМФОРТ", "COMFORT"));
            addSwitch(list, tr("Меньше анимации", "Менш анімацыі", "Reduce motion"), "reduced_motion", false);
            note(list, tr("Переходы панелей короче. Системное отключение анимаций учитывается всегда.", "Пераходы панэляў карацейшыя. Сістэмнае адключэнне анімацый улічваецца заўсёды.", "Shorter panel transitions. System animation settings are always respected."));
            addSwitch(list, tr("Лёгкий виброотклик", "Лёгкі вібраадгук", "Gentle haptics"), "haptics", false);
            section(list, tr("WEQROM", "WEQROM", "WEQROM"));
            addRow(list, tr("Дополнительные функции", "Дадатковыя функцыі", "More features"),
                tr("Настройки исходного клиента", "Налады зыходнага кліента", "Original client settings"),
                new View.OnClickListener() { public void onClick(View v) { openAdvanced(); } });
            if (settings) addRow(list, tr("Показать приветствие", "Паказаць прывітанне", "Replay welcome"), "", new View.OnClickListener() {
                public void onClick(View v) {
                    prefs.edit().putBoolean("welcome_complete", false).apply();
                    close();
                    showWelcome(activity);
                }
            });
            note(list, tr("Настройки сохраняются на телефоне. Без фонового сервиса и постоянных анимаций.", "Налады захоўваюцца на тэлефоне. Без фонавага сэрвісу і пастаянных анімацый.", "Settings stay on your phone. No background service or continuous animation."));
        }

        String colorName(int index) {
            String[] ru = {"Красный", "Синий", "Лайм", "Фиолетовый", "Оранжевый", "Розовый"};
            String[] be = {"Чырвоны", "Сіні", "Лайм", "Фіялетавы", "Аранжавы", "Ружовы"};
            String[] en = {"Red", "Blue", "Lime", "Violet", "Orange", "Pink"};
            return tr(ru[index], be[index], en[index]);
        }

        void regionPicker() {
            final String[] names = new String[REGION_CODES.length];
            for (int i = 0; i < names.length; i++) names[i] = regionName(REGION_CODES[i]);
            picker(tr("Регион", "Рэгіён", "Region"), names, indexOf(REGION_CODES, region(activity)), new Selection() {
                public void selected(int which) { prefs.edit().putString("region", REGION_CODES[which]).apply(); apply(); render(); }
            });
        }

        void languagePicker() {
            picker(tr("Язык Weqrom", "Мова Weqrom", "Weqrom language"), LANGUAGE_NAMES, indexOf(LANGUAGE_CODES, language(activity)), new Selection() {
                public void selected(int which) { prefs.edit().putString("language", LANGUAGE_CODES[which]).apply(); apply(); render(); }
            });
        }

        void themePicker() {
            final String[] themes = {"dark", "light", "system"};
            String[] names = {tr("Тёмное", "Цёмнае", "Dark"), tr("Светлое", "Светлае", "Light"), tr("Как в системе", "Як у сістэме", "Follow system")};
            picker(tr("Тема", "Тэма", "Theme"), names, indexOf(themes, theme(activity)), new Selection() {
                public void selected(int which) {
                    prefs.edit().putString("theme", themes[which]).apply();
                    apply();
                    render();
                    Window w = dialog.getWindow();
                    if (w != null) {
                        w.setStatusBarColor(background);
                        w.setNavigationBarColor(background);
                        int flags = View.SYSTEM_UI_FLAG_LAYOUT_STABLE | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN;
                        if (!dark()) flags |= View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
                        w.getDecorView().setSystemUiVisibility(flags);
                    }
                }
            });
        }

        interface Selection { void selected(int which); }

        void picker(String title, String[] names, int selected, final Selection action) {
            if (pickerDialog != null) pickerDialog.dismiss();
            pickerDialog = new AlertDialog.Builder(activity, dark() ? android.R.style.Theme_Material_Dialog_Alert : android.R.style.Theme_Material_Light_Dialog_Alert)
                .setTitle(title).setSingleChoiceItems(names, selected, new DialogInterface.OnClickListener() {
                    public void onClick(DialogInterface d, int which) { action.selected(which); d.dismiss(); }
                }).setNegativeButton(tr("Отмена", "Скасаваць", "Cancel"), null).create();
            pickerDialog.setOnDismissListener(new DialogInterface.OnDismissListener() {
                public void onDismiss(DialogInterface value) { pickerDialog = null; }
            });
            pickerDialog.show();
        }

        void heading(String title, String subtitle) {
            TextView name = text(title, 31, foreground, true);
            LinearLayout.LayoutParams heading = new LinearLayout.LayoutParams(-1, -2);
            heading.topMargin = dp(10);
            body.addView(name, heading);
            TextView detail = text(subtitle, 15, secondary, false);
            detail.setLineSpacing(dp(3), 1f);
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
            params.topMargin = dp(9);
            params.bottomMargin = dp(22);
            body.addView(detail, params);
        }

        void section(LinearLayout parent, String title) {
            TextView label = text(title, 11, secondary, true);
            label.setLetterSpacing(0.11f);
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
            params.topMargin = dp(23);
            params.bottomMargin = dp(10);
            parent.addView(label, params);
        }

        void note(LinearLayout parent, String value) {
            TextView label = text(value, 12, secondary, false);
            label.setLineSpacing(dp(3), 1f);
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
            params.topMargin = dp(8);
            params.bottomMargin = dp(6);
            parent.addView(label, params);
        }

        void addRow(LinearLayout parent, String label, String value, final View.OnClickListener action) {
            LinearLayout row = new LinearLayout(activity);
            row.setOrientation(LinearLayout.HORIZONTAL);
            row.setGravity(Gravity.CENTER_VERTICAL);
            row.setPadding(dp(16), dp(12), dp(14), dp(12));
            row.setBackground(shape(surface, 18, border));
            LinearLayout labels = new LinearLayout(activity);
            labels.setOrientation(LinearLayout.VERTICAL);
            labels.addView(text(label, 16, foreground, true));
            if (value.length() > 0) {
                TextView detail = text(value, 13, secondary, false);
                LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
                params.topMargin = dp(4);
                labels.addView(detail, params);
            }
            row.addView(labels, new LinearLayout.LayoutParams(0, -2, 1f));
            TextView arrow = text("›", 26, secondary, false);
            arrow.setGravity(Gravity.CENTER);
            row.addView(arrow, new LinearLayout.LayoutParams(dp(24), -2));
            row.setOnClickListener(new View.OnClickListener() { public void onClick(View v) { haptic(v); action.onClick(v); } });
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
            params.bottomMargin = dp(8);
            row.setMinimumHeight(dp(64));
            parent.addView(row, params);
        }

        void addSwitch(LinearLayout parent, String title, final String key, boolean initial) {
            LinearLayout row = new LinearLayout(activity);
            row.setGravity(Gravity.CENTER_VERTICAL);
            row.setPadding(dp(16), dp(8), dp(12), dp(8));
            row.setBackground(shape(surface, 18, border));
            TextView label = text(title, 15, foreground, true);
            row.addView(label, new LinearLayout.LayoutParams(0, -2, 1f));
            final Switch control = new Switch(activity);
            control.setChecked(prefs.getBoolean(key, initial));
            control.setContentDescription(title);
            control.setMinHeight(dp(48));
            control.setOnCheckedChangeListener(new android.widget.CompoundButton.OnCheckedChangeListener() {
                public void onCheckedChanged(android.widget.CompoundButton button, boolean value) { prefs.edit().putBoolean(key, value).apply(); apply(); }
            });
            row.addView(control);
            row.setMinimumHeight(dp(64));
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
            params.bottomMargin = dp(8);
            parent.addView(row, params);
        }

        TextView choice(String label, boolean selected) {
            TextView value = text(label + (selected ? "   ✓" : ""), 17, selected ? (dark() ? ACCENT : 0xff2b6f1e) : foreground, selected);
            value.setGravity(Gravity.CENTER_VERTICAL);
            value.setPadding(dp(18), 0, dp(18), 0);
            value.setBackground(shape(surface, 17, selected ? (dark() ? ACCENT : 0xff6aa451) : border));
            value.setContentDescription(label + (selected ? tr(", выбран", ", выбрана", ", selected") : ""));
            return value;
        }

        TextView text(String value, int size, int color, boolean bold) {
            TextView text = new TextView(activity);
            text.setText(value);
            text.setTextSize(size);
            text.setTextColor(color);
            text.setTypeface(Typeface.create("sans-serif", bold ? Typeface.BOLD : Typeface.NORMAL));
            text.setIncludeFontPadding(false);
            return text;
        }

        Button primary(String title) {
            Button button = new Button(activity);
            button.setAllCaps(false);
            button.setText(title);
            button.setTextSize(15);
            button.setTypeface(Typeface.create("sans-serif", Typeface.BOLD));
            button.setTextColor(0xff13220f);
            button.setBackground(shape(ACCENT, 19, 0));
            button.setStateListAnimator(null);
            button.setPadding(dp(12), 0, dp(12), 0);
            button.setMinHeight(dp(54));
            return button;
        }

        void footnote(String value) {
            TextView note = text(value, 11, secondary, false);
            note.setGravity(Gravity.CENTER);
            note.setLineSpacing(dp(3), 1f);
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
            params.topMargin = dp(15);
            body.addView(note, params);
        }

        GradientDrawable shape(int color, int radius, int stroke) {
            GradientDrawable drawable = new GradientDrawable();
            drawable.setColor(color);
            drawable.setCornerRadius(dp(radius));
            if (stroke != 0) drawable.setStroke(dp(1), stroke);
            return drawable;
        }

        void changePage(final int target) {
            handler.removeCallbacks(greetingTick);
            if (greeting != null) greeting.animate().cancel();
            if (!animate()) { page = target; render(); return; }
            body.animate().alpha(0f).translationY(-dp(7)).setDuration(150).withEndAction(new Runnable() {
                public void run() {
                    if (closed) return;
                    page = target;
                    render();
                    body.setAlpha(0f);
                    body.setTranslationY(dp(10));
                    body.animate().alpha(1f).translationY(0f).setDuration(260).setInterpolator(new DecelerateInterpolator()).start();
                }
            }).start();
        }

        boolean animate() {
            if (prefs.getBoolean("reduced_motion", false)) return false;
            try { return Settings.Global.getFloat(activity.getContentResolver(), Settings.Global.ANIMATOR_DURATION_SCALE, 1f) > 0f; }
            catch (RuntimeException ignored) { return false; }
        }

        void haptic(View v) {
            if (prefs.getBoolean("haptics", false)) v.performHapticFeedback(HapticFeedbackConstants.KEYBOARD_TAP);
        }

        void apply() {
            Integration bridge = integration;
            if (bridge == null) return;
            try { bridge.apply(activity, options(activity)); }
            catch (RuntimeException failed) {
                Toast.makeText(activity, tr("Настройки сохранены. Применение к ленте не удалось.", "Налады захаваныя. Ужыць да стужкі не атрымалася.", "Saved. Could not apply settings to the feed."), Toast.LENGTH_SHORT).show();
            }
        }

        void openAdvanced() {
            Integration bridge = integration;
            try {
                if (bridge == null) throw new IllegalStateException("Client settings bridge unavailable");
                bridge.openAdvanced(activity);
            } catch (RuntimeException failed) {
                Toast.makeText(activity, tr("Не удалось открыть дополнительные функции.", "Не атрымалася адкрыць дадатковыя функцыі.", "Could not open additional features."), Toast.LENGTH_SHORT).show();
            }
        }

        boolean regionHook() { Integration bridge = integration; return bridge != null && bridge.hasRegionHook(); }
        boolean themeHook() { Integration bridge = integration; return bridge != null && bridge.hasThemeHook(); }
        boolean likeHook() { Integration bridge = integration; return bridge != null && bridge.hasLikeColorHook(); }

        String regionName(String code) {
            int index = indexOf(REGION_CODES, code);
            if (index < 0) return code;
            if ("ru".equals(language(activity)) || "be".equals(language(activity))) return REGION_NAMES_RU[index] + " · " + code;
            return REGION_NAMES_EN[index] + " · " + code;
        }

        String languageName() {
            int index = indexOf(LANGUAGE_CODES, language(activity));
            return index < 0 ? "English" : LANGUAGE_NAMES[index];
        }

        int dp(float value) { return Math.round(activity.getResources().getDisplayMetrics().density * value); }

        void close() {
            // A hidden Dialog can still own a Window. Dismiss it as well as
            // unregistering callbacks when the host Activity is destroyed.
            dialog.dismiss();
            cleanup();
        }

        void pauseMotion() {
            handler.removeCallbacksAndMessages(null);
            if (greeting != null) {
                greeting.animate().cancel();
                greeting.setAlpha(1f);
                greeting.setTranslationY(0f);
            }
            if (body != null) {
                body.animate().cancel();
                body.setAlpha(1f);
                body.setTranslationY(0f);
            }
            if (root != null) root.animate().cancel();
        }

        void resume() {
            if (closed || activity.isFinishing() || activity.isDestroyed()) return;
            if (hiddenForStop) {
                hiddenForStop = false;
                dialog.show();
            }
            if (page == 0 && dialog.isShowing()) {
                if (greeting != null) { greeting.setAlpha(1f); greeting.setTranslationY(0f); }
                handler.removeCallbacks(greetingTick);
                if (animate()) handler.postDelayed(greetingTick, 2100);
            }
        }

        void cleanup() {
            if (closed) return;
            closed = true;
            hiddenForStop = false;
            pauseMotion();
            if (pickerDialog != null) pickerDialog.dismiss();
            if (lifecycleRegistered) {
                activity.getApplication().unregisterActivityLifecycleCallbacks(this);
                lifecycleRegistered = false;
            }
            if (active.get() == this) active.clear();
        }

        public void onActivityCreated(Activity a, Bundle state) {}
        public void onActivityStarted(Activity a) {}
        public void onActivityResumed(Activity a) {
            if (a == activity) resume();
        }
        public void onActivityPaused(Activity a) {
            if (a == activity) pauseMotion();
        }
        public void onActivityStopped(Activity a) {
            if (a != activity || closed) return;
            if (settings) { close(); return; }
            // Preserve page, selected language and completion callback during
            // Home/app switching. No animation or timer runs while hidden.
            pauseMotion();
            if (pickerDialog != null) pickerDialog.dismiss();
            hiddenForStop = true;
            dialog.hide();
        }
        public void onActivitySaveInstanceState(Activity a, Bundle state) {}
        public void onActivityDestroyed(Activity a) { if (a == activity) close(); }
    }

    private static int indexOf(String[] values, String value) {
        for (int i = 0; i < values.length; i++) if (values[i].equals(value)) return i;
        return -1;
    }
}
