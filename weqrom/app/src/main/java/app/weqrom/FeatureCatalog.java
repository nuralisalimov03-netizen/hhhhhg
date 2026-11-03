package app.weqrom;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/** Optional controls already implemented by the pinned source mod. Defaults are
 * descriptive; loading this table never enables or disables a feature.
 */
public final class FeatureCatalog {
    private FeatureCatalog() {}

    public static final class Entry {
        public final String key;
        public final String titleRussian;
        public final String titleEnglish;
        public final boolean defaultEnabled;

        private Entry(String key, String russian, String english, boolean enabled) {
            this.key = key; titleRussian = russian; titleEnglish = english;
            defaultEnabled = enabled;
        }

        public String title(String language) {
            return "ru".equals(language) || "be".equals(language) ? titleRussian : titleEnglish;
        }
    }

    private static final List<Entry> ENTRIES = Collections.unmodifiableList(Arrays.asList(new Entry[] {
        new Entry("hide_ads", "Убирать рекламу из ленты", "Hide feed ads", true),
        new Entry("hide_lives", "Убирать трансляции", "Hide livestreams", false),
        new Entry("hide_photos", "Убирать публикации с фото", "Hide photo posts", false),
        new Entry("hide_stories", "Убирать истории", "Hide stories", false),
        new Entry("simple_mode", "Минимализм в ленте", "Minimal feed interface", false),
        new Entry("always_show_progress_bar", "Полоса перемотки на видео", "Video progress bar", false),
        new Entry("skip_quality_picker", "Пропускать выбор качества при скачивании", "Skip download quality picker", false),
        new Entry("anti_burnout", "Защита экрана от выгорания", "Screen burn-in protection", false),
        new Entry("anti_burnout_enable_on_start", "Включать защиту экрана при запуске", "Enable screen protection at startup", false),
        new Entry("anti_burnout_hide_system_bars", "Скрывать системные панели в защите экрана", "Hide system bars during screen protection", false)
    }));

    public static List<Entry> all() { return ENTRIES; }

    public static Entry find(String key) {
        for (Entry entry : ENTRIES) if (entry.key.equals(key)) return entry;
        return null;
    }
}
