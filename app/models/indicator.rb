class Indicator < ActiveRecord::Base
  NumBuckets = 7

  #has_many(:indicator_region_types)
  #has_many(:region_types, :through => :indicator_region_types)
  has_many(:indicator_region_values, :dependent => :delete_all)

  # Casts a value to the type this Indicator uses (integer or float)
  def cast_value(value)
    case value_type
    when 'integer' then value.to_i
    when 'float' then value.to_f
    else raise_invalid_type
    end
  end

  # Returns the value from the given IndicatorRegionValue object
  def get_value(object)
    case value_type
    when 'integer' then object.value_integer
    when 'float' then object.value_float
    else raise_invalid_type
    end
  end

  # Sets the value on the given IndicatorRegionValue object
  def set_value(object, value)
    value = cast_value(value)

    case value_type
    when 'integer' then object.value_integer = value
    when 'float' then object.value_float = value
    else raise_invalid_type
    end
  end

  # Returns the value column to use in the region_indicator_values table.
  #
  # Will be 'value_integer' or 'value_float'
  def value_column
    case value_type
    when 'integer' then 'value_integer'
    when 'float' then 'value_float'
    else raise_invalid_type
    end
  end

  def create_all_possible_indicator_region_values
    select_sql = sql.dup
    wheres = []

    keys = sql.scan(/{[^}]*?}/)
    keys.sort!
    keys.uniq!

    raise ArgumentError.new("This Indicator's \"sql\" column isn't a formula containing other indicators that look like \"{Other indicator}\". Either fix the \"sql\" property (currently #{sql.inspect}) or don't call this method.") if keys.empty?

    keys.each do |key_with_brackets|
      key = key_with_brackets[1...-1]
      other_indicator = Indicator.find_by_name(key)
      raise ArgumentError.new("Unknown indicator #{key.inspect} found in \"sql\" column (currently #{sql}).") if other_indicator.nil?

      sql.gsub!(/{#{Regexp.quote(key)}}/) do |x|
        "(SELECT #{other_indicator.value_column} FROM indicator_region_values x WHERE x.indicator_id = #{other_indicator.id} AND x.region_id = indicator_region_values.region_id AND x.year = indicator_region_values.year)"
      end

      if wheres.empty?
        wheres << "indicator_region_values.indicator_id = #{other_indicator.id}"
      else
        wheres << "(indicator_region_values.region_id, indicator_region_values.year) IN (SELECT region_id, year FROM indicator_region_values WHERE indicator_id = #{other_indicator.id})"
      end
    end

    q = "INSERT INTO indicator_region_values (indicator_id, region_id, year, #{value_column}, note) SELECT #{id}, region_id, year, #{sql}, note FROM indicator_region_values WHERE #{wheres.join(' AND ')}"

    IndicatorRegionValue.connection.execute(q)
  end

  def set_sensible_buckets!
    q = "SELECT DISTINCT #{value_column} FROM indicator_region_values WHERE indicator_id = #{id} ORDER BY #{value_column}"

    values = IndicatorRegionValue.connection.select_values(q)

    num_per_bucket = (1.0 * values.length / NumBuckets).ceil

    buckets = values.in_groups_of(num_per_bucket)

    bucket_strings = buckets.map do |bucket|
      lowest = bucket.first
      highest = bucket.compact.last

      if lowest == highest
        "#{lowest}"
      else
        "#{lowest} to #{highest}"
      end
    end

    buckets_string = bucket_strings.join(';')

    self.buckets = buckets_string
  end

  protected

  def raise_invalid_type
    raise Exception.new("Invalid value_type #{value_type.inspect}")
  end
end
