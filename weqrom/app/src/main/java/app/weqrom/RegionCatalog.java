package app.weqrom;

import java.text.Normalizer;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.regex.Pattern;

/** The complete 191-country table from the pinned TikTok You mod, usable before
 * its dynamic DEX has loaded. Index matches Z.ue.a; id is its legacy stable id.
 * This table describes local SIM/operator overrides, not account countries.
 */
public final class RegionCatalog {
    private RegionCatalog() {}
    public static final String SOURCE_SHA256 = "9f34642dd5d62f96cc84bdf2a1e44f198f78fb5e813944ac1058714abef551bc";
    private static final Pattern MARKS = Pattern.compile("\\p{M}+");

    public static final class Entry {
        public final int index;
        public final int id;
        public final String iso;
        public final String nameEnglish;
        public final String nameRussian;
        public final String mccMnc;
        public final String operator;
        private final String searchBase;

        private Entry(int index, int id, String iso, String nameEnglish,
                String nameRussian, String mccMnc, String operator) {
            this.index = index; this.id = id; this.iso = iso;
            this.nameEnglish = nameEnglish; this.nameRussian = nameRussian;
            this.mccMnc = mccMnc; this.operator = operator;
            searchBase = normalize(iso + " " + nameEnglish + " " + nameRussian);
        }

        public String label(String language) {
            if ("ru".equals(language)) return nameRussian;
            if (language == null || language.isEmpty() || "en".equals(language)) return nameEnglish;
            String localized = new Locale("", iso).getDisplayCountry(new Locale(language));
            return localized.isEmpty() || localized.equals(iso) ? nameEnglish : localized;
        }
    }

    private static final List<Entry> ENTRIES = Collections.unmodifiableList(Arrays.asList(new Entry[] {
        new Entry(0, 0, "AU", "Australia", "Австралия", "50501", "Telstra"),
        new Entry(1, 1, "AT", "Austria", "Австрия", "23201", "A1 Telekom"),
        new Entry(2, 2, "AZ", "Azerbaijan", "Азербайджан", "40001", "Azercell"),
        new Entry(3, 3, "AL", "Albania", "Албания", "27601", "ONE"),
        new Entry(4, 4, "DZ", "Algeria", "Алжир", "60301", "Mobilis"),
        new Entry(5, 5, "AO", "Angola", "Ангола", "63102", "Unitel"),
        new Entry(6, 6, "AD", "Andorra", "Андорра", "21303", "Andorra Telecom"),
        new Entry(7, 7, "AG", "Antigua and Barbuda", "Антигуа и Барбуда", "344920", "Flow"),
        new Entry(8, 8, "AR", "Argentina", "Аргентина", "722310", "Claro"),
        new Entry(9, 9, "AM", "Armenia", "Армения", "28305", "Team Telecom"),
        new Entry(10, 11, "BS", "Bahamas", "Багамы", "364030", "BTC"),
        new Entry(11, 12, "BD", "Bangladesh", "Бангладеш", "47001", "Grameenphone"),
        new Entry(12, 13, "BB", "Barbados", "Барбадос", "342600", "Flow"),
        new Entry(13, 14, "BY", "Belarus", "Беларусь", "25701", "A1"),
        new Entry(14, 15, "BZ", "Belize", "Белиз", "70267", "DigiCell"),
        new Entry(15, 16, "BE", "Belgium", "Бельгия", "20601", "Proximus"),
        new Entry(16, 17, "BJ", "Benin", "Бенин", "61603", "MTN"),
        new Entry(17, 18, "BM", "Bermuda", "Бермуды", "35000", "One Communications"),
        new Entry(18, 19, "BG", "Bulgaria", "Болгария", "28401", "A1 Bulgaria"),
        new Entry(19, 20, "BO", "Bolivia", "Боливия", "73602", "Entel"),
        new Entry(20, 21, "BA", "Bosnia and Herzegovina", "Босния и Герцеговина", "21805", "m:tel"),
        new Entry(21, 22, "BW", "Botswana", "Ботсвана", "65201", "Mascom"),
        new Entry(22, 23, "BR", "Brazil", "Бразилия", "72406", "Vivo"),
        new Entry(23, 24, "BN", "Brunei", "Бруней", "52811", "DST"),
        new Entry(24, 25, "BF", "Burkina Faso", "Буркина-Фасо", "61301", "Onatel"),
        new Entry(25, 26, "BI", "Burundi", "Бурунди", "64201", "Econet"),
        new Entry(26, 27, "BT", "Bhutan", "Бутан", "40211", "B-Mobile"),
        new Entry(27, 28, "VU", "Vanuatu", "Вануату", "54105", "Digicel"),
        new Entry(28, 30, "GB", "United Kingdom", "Великобритания", "23410", "O2"),
        new Entry(29, 31, "HU", "Hungary", "Венгрия", "21630", "Telekom"),
        new Entry(30, 32, "VE", "Venezuela", "Венесуэла", "73404", "Movistar"),
        new Entry(31, 33, "TL", "Timor-Leste", "Восточный Тимор", "51402", "Telemor"),
        new Entry(32, 34, "VN", "Vietnam", "Вьетнам", "45204", "Viettel"),
        new Entry(33, 35, "GA", "Gabon", "Габон", "62801", "Libertis"),
        new Entry(34, 36, "HT", "Haiti", "Гаити", "37203", "Natcom"),
        new Entry(35, 37, "GY", "Guyana", "Гайана", "73801", "GTT"),
        new Entry(36, 38, "GM", "Gambia", "Гамбия", "60702", "Africell"),
        new Entry(37, 39, "GH", "Ghana", "Гана", "62001", "MTN"),
        new Entry(38, 40, "GP", "Guadeloupe", "Гваделупа", "34001", "Orange"),
        new Entry(39, 41, "GT", "Guatemala", "Гватемала", "70402", "Tigo"),
        new Entry(40, 42, "GN", "Guinea", "Гвинея", "61101", "Orange"),
        new Entry(41, 43, "GW", "Guinea-Bissau", "Гвинея-Бисау", "63203", "Orange"),
        new Entry(42, 44, "DE", "Germany", "Германия", "26201", "Telekom"),
        new Entry(43, 45, "HN", "Honduras", "Гондурас", "708002", "Tigo"),
        new Entry(44, 46, "HK", "Hong Kong", "Гонконг", "45400", "CSL"),
        new Entry(45, 47, "GD", "Grenada", "Гренада", "352110", "Flow"),
        new Entry(46, 48, "GL", "Greenland", "Гренландия", "29001", "Tele Greenland"),
        new Entry(47, 49, "GR", "Greece", "Греция", "20201", "Cosmote"),
        new Entry(48, 50, "GE", "Georgia", "Грузия", "28202", "MagtiCom"),
        new Entry(49, 51, "DK", "Denmark", "Дания", "23801", "TDC"),
        new Entry(50, 52, "DJ", "Djibouti", "Джибути", "63801", "Evatis"),
        new Entry(51, 53, "DM", "Dominica", "Доминика", "366110", "Flow"),
        new Entry(52, 54, "DO", "Dominican Republic", "Доминиканская Республика", "37001", "Claro"),
        new Entry(53, 55, "CD", "DR Congo", "Демократическая Республика Конго", "63001", "Vodacom"),
        new Entry(54, 56, "EG", "Egypt", "Египет", "60202", "Vodafone"),
        new Entry(55, 57, "ZM", "Zambia", "Замбия", "64501", "Airtel"),
        new Entry(56, 58, "ZW", "Zimbabwe", "Зимбабве", "64804", "Econet"),
        new Entry(57, 59, "IL", "Israel", "Израиль", "42501", "Partner"),
        new Entry(58, 61, "ID", "Indonesia", "Индонезия", "51010", "Telkomsel"),
        new Entry(59, 62, "JO", "Jordan", "Иордания", "41601", "Zain"),
        new Entry(60, 63, "IQ", "Iraq", "Ирак", "41805", "Asiacell"),
        new Entry(61, 65, "IE", "Ireland", "Ирландия", "27201", "Vodafone"),
        new Entry(62, 66, "IS", "Iceland", "Исландия", "27401", "Síminn"),
        new Entry(63, 67, "ES", "Spain", "Испания", "21401", "Movistar"),
        new Entry(64, 68, "IT", "Italy", "Италия", "22201", "TIM"),
        new Entry(65, 69, "CV", "Cape Verde", "Кабо-Верде", "62501", "CV Móvel"),
        new Entry(66, 70, "KZ", "Kazakhstan", "Казахстан", "40101", "Beeline"),
        new Entry(67, 71, "KH", "Cambodia", "Камбоджа", "45601", "Cellcard"),
        new Entry(68, 72, "CM", "Cameroon", "Камерун", "62401", "MTN"),
        new Entry(69, 73, "CA", "Canada", "Канада", "302720", "Rogers"),
        new Entry(70, 74, "QA", "Qatar", "Катар", "42701", "Ooredoo"),
        new Entry(71, 75, "KE", "Kenya", "Кения", "63902", "Safaricom"),
        new Entry(72, 76, "CY", "Cyprus", "Кипр", "28001", "Cytamobile-Vodafone"),
        new Entry(73, 77, "KI", "Kiribati", "Кирибати", "54501", "ATHKL"),
        new Entry(74, 79, "CO", "Colombia", "Колумбия", "732101", "Claro"),
        new Entry(75, 80, "KM", "Comoros", "Коморы", "65401", "Comores Telecom"),
        new Entry(76, 82, "CR", "Costa Rica", "Коста-Рика", "71201", "ICE"),
        new Entry(77, 83, "CI", "Ivory Coast", "Кот-д'Ивуар", "61203", "Orange"),
        new Entry(78, 85, "KW", "Kuwait", "Кувейт", "41902", "Zain"),
        new Entry(79, 87, "LA", "Laos", "Лаос", "45701", "Lao Telecom"),
        new Entry(80, 88, "LV", "Latvia", "Латвия", "24701", "LMT"),
        new Entry(81, 89, "LS", "Lesotho", "Лесото", "65101", "Vodacom"),
        new Entry(82, 90, "LR", "Liberia", "Либерия", "61801", "Lonestar"),
        new Entry(83, 91, "LB", "Lebanon", "Ливан", "41501", "Alfa"),
        new Entry(84, 92, "LT", "Lithuania", "Литва", "24601", "Telia"),
        new Entry(85, 93, "LI", "Liechtenstein", "Лихтенштейн", "29501", "Swisscom"),
        new Entry(86, 94, "LU", "Luxembourg", "Люксембург", "27001", "POST"),
        new Entry(87, 95, "MU", "Mauritius", "Маврикий", "61701", "my.t"),
        new Entry(88, 96, "MR", "Mauritania", "Мавритания", "60901", "Mauritel"),
        new Entry(89, 97, "MG", "Madagascar", "Мадагаскар", "64604", "Telma"),
        new Entry(90, 98, "YT", "Mayotte", "Майотта", "64710", "SFR"),
        new Entry(91, 99, "MO", "Macau", "Макао", "45501", "CTM"),
        new Entry(92, 100, "MW", "Malawi", "Малави", "65001", "TNM"),
        new Entry(93, 101, "MY", "Malaysia", "Малайзия", "50212", "Maxis"),
        new Entry(94, 102, "ML", "Mali", "Мали", "61001", "Malitel"),
        new Entry(95, 103, "MV", "Maldives", "Мальдивы", "47201", "Dhiraagu"),
        new Entry(96, 104, "MT", "Malta", "Мальта", "27801", "Epic"),
        new Entry(97, 105, "MA", "Morocco", "Марокко", "60401", "Maroc Telecom"),
        new Entry(98, 106, "MQ", "Martinique", "Мартиника", "34001", "Orange"),
        new Entry(99, 107, "MH", "Marshall Islands", "Маршалловы Острова", "55101", "NTA"),
        new Entry(100, 108, "MX", "Mexico", "Мексика", "334020", "Telcel"),
        new Entry(101, 109, "FM", "Micronesia", "Микронезия", "55001", "FSMTC"),
        new Entry(102, 110, "MZ", "Mozambique", "Мозамбик", "64301", "mcel"),
        new Entry(103, 111, "MD", "Moldova", "Молдова", "25901", "Orange"),
        new Entry(104, 112, "MC", "Monaco", "Монако", "21201", "Monaco Telecom"),
        new Entry(105, 113, "MN", "Mongolia", "Монголия", "42899", "Mobicom"),
        new Entry(106, 114, "MM", "Myanmar", "Мьянма", "41401", "MPT"),
        new Entry(107, 115, "NA", "Namibia", "Намибия", "64901", "MTC"),
        new Entry(108, 116, "NR", "Nauru", "Науру", "53602", "Digicel"),
        new Entry(109, 117, "NP", "Nepal", "Непал", "42901", "NTC"),
        new Entry(110, 118, "NE", "Niger", "Нигер", "61402", "Airtel"),
        new Entry(111, 119, "NG", "Nigeria", "Нигерия", "62130", "MTN"),
        new Entry(112, 120, "NL", "Netherlands", "Нидерланды", "20404", "Vodafone"),
        new Entry(113, 121, "NI", "Nicaragua", "Никарагуа", "71021", "Claro"),
        new Entry(114, 122, "NZ", "New Zealand", "Новая Зеландия", "53001", "One NZ"),
        new Entry(115, 123, "NC", "New Caledonia", "Новая Каледония", "54601", "Mobilis"),
        new Entry(116, 124, "NO", "Norway", "Норвегия", "24201", "Telenor"),
        new Entry(117, 125, "AE", "United Arab Emirates", "Объединенные Арабские Эмираты", "42402", "Etisalat"),
        new Entry(118, 126, "OM", "Oman", "Оман", "42202", "Omantel"),
        new Entry(119, 127, "PK", "Pakistan", "Пакистан", "41001", "Jazz"),
        new Entry(120, 128, "PW", "Palau", "Палау", "55201", "PNCC"),
        new Entry(121, 129, "PA", "Panama", "Панама", "71401", "Cable & Wireless"),
        new Entry(122, 130, "PG", "Papua New Guinea", "Папуа-Новая Гвинея", "53701", "Digicel"),
        new Entry(123, 131, "PY", "Paraguay", "Парагвай", "74404", "Tigo"),
        new Entry(124, 132, "PE", "Peru", "Перу", "71610", "Movistar"),
        new Entry(125, 133, "PL", "Poland", "Польша", "26003", "Orange"),
        new Entry(126, 134, "PT", "Portugal", "Португалия", "26801", "MEO"),
        new Entry(127, 135, "PR", "Puerto Rico", "Пуэрто-Рико", "330110", "Claro"),
        new Entry(128, 136, "CG", "Republic of the Congo", "Республика Конго", "62910", "MTN"),
        new Entry(129, 137, "RE", "Reunion", "Реюньон", "64700", "Orange"),
        new Entry(130, 139, "RW", "Rwanda", "Руанда", "63510", "MTN"),
        new Entry(131, 140, "RO", "Romania", "Румыния", "22610", "Orange"),
        new Entry(132, 141, "SV", "El Salvador", "Эль-Сальвадор", "70603", "Tigo"),
        new Entry(133, 142, "WS", "Samoa", "Самоа", "54901", "Digicel"),
        new Entry(134, 143, "SM", "San Marino", "Сан-Марино", "29201", "TMS"),
        new Entry(135, 144, "ST", "Sao Tome and Principe", "Сан-Томе и Принсипи", "62601", "CST"),
        new Entry(136, 145, "SA", "Saudi Arabia", "Саудовская Аравия", "42001", "STC"),
        new Entry(137, 146, "MK", "North Macedonia", "Северная Македония", "29401", "Makedonski Telekom"),
        new Entry(138, 147, "SC", "Seychelles", "Сейшелы", "63301", "Cable & Wireless"),
        new Entry(139, 148, "SN", "Senegal", "Сенегал", "60801", "Orange"),
        new Entry(140, 149, "VC", "St. Vincent and the Grenadines", "Сент-Винсент и Гренадины", "360110", "Flow"),
        new Entry(141, 150, "KN", "Saint Kitts and Nevis", "Сент-Китс и Невис", "356110", "Flow"),
        new Entry(142, 151, "LC", "Saint Lucia", "Сент-Люсия", "358110", "Flow"),
        new Entry(143, 152, "RS", "Serbia", "Сербия", "22003", "mts"),
        new Entry(144, 153, "SG", "Singapore", "Сингапур", "52501", "Singtel"),
        new Entry(145, 154, "SK", "Slovakia", "Словакия", "23101", "Orange"),
        new Entry(146, 155, "SI", "Slovenia", "Словения", "29341", "Telekom Slovenije"),
        new Entry(147, 156, "SB", "Solomon Islands", "Соломоновы Острова", "54001", "Our Telekom"),
        new Entry(148, 158, "SD", "Sudan", "Судан", "63401", "Zain"),
        new Entry(149, 159, "SR", "Suriname", "Суринам", "74602", "Telesur"),
        new Entry(150, 160, "US", "United States", "США", "310260", "T-Mobile"),
        new Entry(151, 161, "SL", "Sierra Leone", "Сьерра-Леоне", "61901", "Orange"),
        new Entry(152, 162, "TJ", "Tajikistan", "Таджикистан", "43601", "Tcell"),
        new Entry(153, 163, "TH", "Thailand", "Таиланд", "52001", "AIS"),
        new Entry(154, 165, "TZ", "Tanzania", "Танзания", "64004", "Vodacom"),
        new Entry(155, 166, "TG", "Togo", "Того", "61501", "Togocel"),
        new Entry(156, 167, "TO", "Tonga", "Тонга", "53943", "Digicel"),
        new Entry(157, 168, "TT", "Trinidad and Tobago", "Тринидад и Тобаго", "374130", "Digicel"),
        new Entry(158, 169, "TV", "Tuvalu", "Тувалу", "55301", "TTC"),
        new Entry(159, 170, "TN", "Tunisia", "Тунис", "60502", "Tunisie Telecom"),
        new Entry(160, 172, "TR", "Turkey", "Турция", "28601", "Turkcell"),
        new Entry(161, 173, "UG", "Uganda", "Уганда", "64110", "MTN"),
        new Entry(162, 174, "UZ", "Uzbekistan", "Узбекистан", "43404", "Beeline"),
        new Entry(163, 175, "UA", "Ukraine", "Украина", "25503", "Kyivstar"),
        new Entry(164, 176, "UY", "Uruguay", "Уругвай", "74801", "Antel"),
        new Entry(165, 177, "FJ", "Fiji", "Фиджи", "54201", "Vodafone"),
        new Entry(166, 178, "PH", "Philippines", "Филиппины", "51502", "Globe"),
        new Entry(167, 179, "FI", "Finland", "Финляндия", "24405", "Elisa"),
        new Entry(168, 180, "FR", "France", "Франция", "20801", "Orange"),
        new Entry(169, 181, "GF", "French Guiana", "Французская Гвиана", "34001", "Orange"),
        new Entry(170, 182, "PF", "French Polynesia", "Французская Полинезия", "54720", "Vini"),
        new Entry(171, 183, "HR", "Croatia", "Хорватия", "21901", "Hrvatski Telekom"),
        new Entry(172, 184, "CF", "Central African Republic", "Центральноафриканская Республика", "62301", "Telecel"),
        new Entry(173, 185, "TD", "Chad", "Чад", "62201", "Airtel"),
        new Entry(174, 186, "ME", "Montenegro", "Черногория", "29703", "m:tel"),
        new Entry(175, 187, "CZ", "Czechia", "Чехия", "23001", "T-Mobile"),
        new Entry(176, 188, "CL", "Chile", "Чили", "73001", "Entel"),
        new Entry(177, 189, "CH", "Switzerland", "Швейцария", "22801", "Swisscom"),
        new Entry(178, 190, "SE", "Sweden", "Швеция", "24001", "Telia"),
        new Entry(179, 191, "LK", "Sri Lanka", "Шри-Ланка", "41301", "Mobitel"),
        new Entry(180, 192, "EC", "Ecuador", "Эквадор", "74001", "Claro"),
        new Entry(181, 193, "GQ", "Equatorial Guinea", "Экваториальная Гвинея", "62701", "Getesa"),
        new Entry(182, 194, "ER", "Eritrea", "Эритрея", "65701", "EriTel"),
        new Entry(183, 195, "SZ", "Eswatini", "Эсватини", "65310", "MTN"),
        new Entry(184, 196, "EE", "Estonia", "Эстония", "24801", "Telia"),
        new Entry(185, 197, "ET", "Ethiopia", "Эфиопия", "63601", "Ethio Telecom"),
        new Entry(186, 198, "ZA", "South Africa", "Южная Африка", "65501", "Vodacom"),
        new Entry(187, 199, "KR", "South Korea", "Южная Корея", "45005", "SK Telecom"),
        new Entry(188, 200, "SS", "South Sudan", "Южный Судан", "65902", "MTN"),
        new Entry(189, 201, "JM", "Jamaica", "Ямайка", "338050", "Digicel"),
        new Entry(190, 202, "JP", "Japan", "Япония", "44010", "NTT docomo"),
    }));

    public static List<Entry> all() { return ENTRIES; }

    public static Entry find(String iso) {
        if (iso == null) return null;
        for (Entry entry : ENTRIES) if (entry.iso.equalsIgnoreCase(iso)) return entry;
        return null;
    }

    private static String normalize(String value) {
        return MARKS.matcher(Normalizer.normalize(value, Normalizer.Form.NFD))
            .replaceAll("").toLowerCase(Locale.ROOT);
    }

    /** Small in-memory search; caller can debounce text input, no network work. */
    public static List<Entry> search(String query, String language) {
        String needle = normalize(query == null ? "" : query.trim());
        if (needle.isEmpty()) return ENTRIES;
        ArrayList<Entry> result = new ArrayList<Entry>();
        for (Entry entry : ENTRIES) {
            if (entry.searchBase.contains(needle)
                    || normalize(entry.label(language)).contains(needle)) result.add(entry);
        }
        return Collections.unmodifiableList(result);
    }
}
