package dev.regresslab.demo;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** A small event dashboard query model. Construction is excluded from query timing. */
public final class EventStatistics {
    private final Map<String, Integer> counts;

    public EventStatistics(List<String> categories) {
        counts = new HashMap<>();
        for (String category : categories) counts.merge(category, 1, Integer::sum);
    }

    public int countByCategory(String category) {
        for (var entry : counts.entrySet()) {
            if (entry.getKey().equals(category)) return entry.getValue();
        }
        return 0;
    }
    public int distinctCategories() { return counts.size(); }
}
