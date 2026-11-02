require "json"
module Minitest
  class LeerieReportReporter < AbstractReporter
    def initialize(path)
      @path = path
      @cases = []
    end

    def record(r)
      st = r.skipped? ? "skipped" : (r.error? ? "error" : (r.passed? ? "passed" : "failed"))
      loc = r.respond_to?(:source_location) ? r.source_location : nil
      @cases << {id: "#{r.klass}##{r.name}", file: loc && loc.first, status: st}
    end

    def report
      File.write(@path, JSON.generate({cases: @cases}))
    end
  end

  def self.plugin_leeriereport_init(_opts)
    p = ENV["LEERIE_MT_REPORT"]
    reporter << LeerieReportReporter.new(p) if p && !p.empty?
  end
end
