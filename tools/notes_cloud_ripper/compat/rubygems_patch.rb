begin
  require "rubygems/resolver/api_set"
  if defined?(Gem::Resolver::APISet) &&
     Gem::Resolver::APISet.const_defined?(:Parser) &&
     !Gem::Resolver::APISet.const_defined?(:GemParser)
    Gem::Resolver::APISet.const_set(:GemParser, Gem::Resolver::APISet::Parser)
  end
rescue LoadError
  # Leave untouched – legacy RubyGems versions may not expose APISet
end
