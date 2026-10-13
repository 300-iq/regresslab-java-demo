package dev.regresslab.demo;

import java.util.List;

/** Intentional demonstration: save the precomputed index, do aggregation per query. */
public final class EventStatistics {
    private final List<String> categories;

    public EventStatistics(List<String> categories) { this.categories = List.copyOf(categories); }

    public int countByCategory(String category) {
        return (int) categories.stream().filter(category::equals).count();
    }

    public int distinctCategories() { return (int) categories.stream().distinct().count(); }
}
