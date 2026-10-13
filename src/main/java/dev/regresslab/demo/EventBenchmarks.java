package dev.regresslab.demo;

import java.util.ArrayList;
import java.util.concurrent.TimeUnit;
import org.openjdk.jmh.annotations.*;

@BenchmarkMode(Mode.AverageTime)
@OutputTimeUnit(TimeUnit.NANOSECONDS)
@State(Scope.Thread)
public class EventBenchmarks {
    @Param({"10000"}) public int events;
    private EventStatistics statistics;
    private String[] categories;
    private int cursor;

    @Setup(Level.Trial)
    public void setup() {
        categories = new String[128];
        for (int i = 0; i < categories.length; i++) categories[i] = "category-" + i;
        var input = new ArrayList<String>(events);
        for (int i = 0; i < events; i++) input.add(categories[(i * 31) % categories.length]);
        statistics = new EventStatistics(input);
    }

    @Benchmark
    public int countByCategory() {
        // Rotate across present keys, preserving stable work rather than timing a constant.
        return statistics.countByCategory(categories[(cursor++) & 127]);
    }

    @Benchmark
    public int distinctCategories() { return statistics.distinctCategories(); }
}
