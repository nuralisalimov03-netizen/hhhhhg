package app.weqrom.ai;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import java.nio.charset.StandardCharsets;

/** Exactly the no-system, no-BOS ChatML format used by the scratch training. */
public final class PromptFormatter {
    public final String id, prompt;
    public final int maxTokens;
    private PromptFormatter(String id, String prompt, int maxTokens) {
        this.id = id; this.prompt = prompt; this.maxTokens = maxTokens;
    }
    public static PromptFormatter parse(String request) throws JSONException {
        if (request == null || request.length() > 65536) throw new IllegalArgumentException("Запрос слишком большой.");
        JSONObject data = new JSONObject(request);
        Object idValue = data.opt("id");
        if (!(idValue instanceof String) || ((String) idValue).isEmpty() || ((String) idValue).length() > 128)
            throw new IllegalArgumentException("Некорректный идентификатор запроса.");
        JSONArray messages = data.getJSONArray("messages");
        if (messages.length() < 1 || messages.length() > 24 || messages.length() % 2 != 1)
            throw new IllegalArgumentException("Передайте до 24 чередующихся сообщений, завершённых вопросом.");
        StringBuilder prompt = new StringBuilder();
        for (int index = 0; index < messages.length(); ++index) {
            JSONObject message = messages.getJSONObject(index);
            String role = message.getString("role");
            String expected = index % 2 == 0 ? "user" : "assistant";
            if (!expected.equals(role)) throw new IllegalArgumentException("Допустимы только чередующиеся роли user и assistant.");
            Object content = message.opt("content");
            if (!(content instanceof String) || ((String) content).trim().isEmpty() || ((String) content).length() > 16384)
                throw new IllegalArgumentException("Сообщение пустое или слишком большое.");
            prompt.append("<|im_start|>").append(role).append('\n').append(safeContent((String) content)).append("<|im_end|>\n");
        }
        prompt.append("<|im_start|>assistant\n");
        if (prompt.toString().getBytes(StandardCharsets.UTF_8).length > 32768)
            throw new IllegalArgumentException("История слишком большая. Начните новый диалог.");
        Object count = data.opt("maxTokens");
        int maxTokens = 256;
        if (count != null && count != JSONObject.NULL) {
            if (!(count instanceof Number)) throw new IllegalArgumentException("Некорректная длина ответа.");
            maxTokens = ((Number) count).intValue();
        }
        return new PromptFormatter((String) idValue, prompt.toString(), Math.max(16, Math.min(512, maxTokens)));
    }
    static String safeContent(String content) {
        // Keep ordinary HTML verbatim. Only tokenizer control syntax is made inert.
        return content.replace("<|", "‹|").replace("|>", "|›")
            .replace("<s>", "‹s›").replace("</s>", "‹/s›").replace("<unk>", "‹unk›").replace("<pad>", "‹pad›");
    }
}
