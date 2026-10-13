package dev.regresslab.demo;
import java.util.List;
public final class EventStatisticsTest {
    public static void main(String[] args) {
        var statistics = new EventStatistics(List.of("alpha", "alpha", "beta", "gamma"));
        if (statistics.countByCategory("alpha") != 2 || statistics.countByCategory("missing") != 0
                || statistics.distinctCategories() != 3 || new EventStatistics(List.of()).distinctCategories() != 0)
            throw new AssertionError("Query semantics changed");
        System.out.println("Event query correctness checks passed.");
    }
}
