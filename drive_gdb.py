import collections, os, re, sys, signal, traceback
import colors
 
DEFAULT_EXPLANATION_URL = "https://comp1511unsw.github.io/dcc/"

#
# Code below is executed from gdb.
# It prints details of the program state likely to be of interest to
# a beginner programmer
#


hash_define = collections.defaultdict(dict)
source = {}

def drive_gdb():
	global debug
	debug = int(os.environ.get('DCC_DEBUG', '0'))
	output_stream = os.fdopen(3, "w")
	colorize_output = output_stream.isatty() or os.environ.get('DCC_COLORIZE_OUTPUT', False)
	if colorize_output:
		color = colors.color
	else:
		color = lambda text, color_name: text
	signal.signal(signal.SIGINT, interrupt_handler)

	try:
		gdb_attach()
		explain_error(output_stream, color)
	except gdb.error as e:
		if 'ptrace' in str(e).lower() and os.path.exists('/.dockerenv'):
			print('\ndcc : can not provide information about variables because docker not run with --cap-add=SYS_PTRACE\n' , file=output_stream)
		elif debug:
			traceback.print_exc(file=output_stream)
		else:
			print(e, file=output_stream)	
		sys.exit(1)
	except:
		if debug: traceback.print_exc(file=output_stream)
		sys.exit(1)

	output_stream.flush()
	gdb_execute('call (void)_exit(1)')
	gdb_execute('quit')
	kill_program1()
	
def gdb_attach():
	pid = None
	pid = int(os.environ.get('DCC_PID', 0))
	if 'DCC_VALGRIND_ERROR' in os.environ:
		debug_print(1, 'attaching gdb to valgrind', pid)
		gdb.execute('target remote | vgdb --pid=%d' % pid)
	else:
		debug_print(1, 'attaching gdb to ', pid)
		gdb.execute('attach %s' % pid)
	
def explain_error(output_stream, color):
	debug_print(1, 'explain_error() starting')
	# file descriptor 3 is a dup of stderr (see below)
	# stdout & stderr have been diverted to /dev/null
	print(file=output_stream)
	loc = gdb_set_frame()
	signal_number = int(os.environ.get('DCC_SIGNAL', signal.SIGABRT))
	if signal_number != signal.SIGABRT:
		 print(explain_signal(signal_number), file=output_stream)
	elif 'DCC_ASAN_ERROR' in os.environ:
		explain_asan_error(loc, output_stream, color)
	elif os.environ.get('DCC_SANITIZER', '') == 'memory':
		if loc:
			print("%s:%d" % (loc.filename, loc.line_number), end=' ', file=output_stream)
		print("runtime error",  color("uninitialized variable used", 'red'),  file=output_stream)

	if loc:
		print(explain_location(loc, color), file=output_stream)
		print(relevant_variables(loc.surrounding_source(color, clean=True), color), file=output_stream)

	gdb.flush(gdb.STDOUT)
	gdb.flush(gdb.STDERR)

def explain_asan_error(loc, output_stream, color):	
	if loc:
		print("%s:%d" % (loc.filename, loc.line_number), end=' ', file=output_stream)
	report = os.environ.get('DCC_ASAN_ERROR')
	if report:
		report = report.replace('-', ' ')
		report = report.replace('heap', 'malloc')
		report = report.replace('null deref', 'NULL pointer derefenced')
	else:
		report = "illegal array, pointer or other operation"
	print('runtime error -', color(report, 'red'), file=output_stream)

	prefix = '\n' + color('dcc explanation:', 'blue') 
	if "malloc buffer overflow" in report:
		print(prefix, """access past the end of malloc'ed memory.
  Make sure you have allocated enough memory for the size of your struct/array.
  A common error is to use the size of a pointer instead of the size of the struct or array.
""", file=output_stream)
	if "stack buffer overflow" in report:
		print(prefix, """access past the end of a local variable.
  Make sure the size of your array is correct.
  Make sure your array indices are correct.
""", file=output_stream)
	elif "use after return" in report:
#		print(prefix, f"""You have used a pointer to a local variable that no longer exists.
#  When a function returns its local variables are destroyed.
#  For more information see: {DEFAULT_EXPLANATION_URL}/stack_use_after_return.html
#""", file=output_stream)
		print(prefix, """You have used a pointer to a local variable that no longer exists.
  When a function returns its local variables are destroyed.
""", file=output_stream)
		print(prefix, 'For more information see:', DEFAULT_EXPLANATION_URL + '/stack_use_after_return.html', file=output_stream)
	elif "use after" in report:
		print(prefix, "access to memory that has already been freed.\n", file=output_stream)
	elif "double free" in report:
		print(prefix, "attempt to free memory that has already been freed.\n", file=output_stream)
	elif "null" in report.lower():
		print(prefix, "attempt to access value using a pointer which is NULL.\n", file=output_stream)
		
def explain_signal(signal_number):
	if signal_number == signal.SIGINT:
		return "Execution was interrupted"
	elif signal_number == signal.SIGFPE:
		return 'Execution stopped by an arithmetic error.\nOften this is caused by division (or %) by zero.'
	elif signal_number == signal.SIGXCPU:
		return "Execution stopped by a CPU time limit."
	elif signal_number == signal.SIGXFSZ:
		return "Execution stopped because too much data written."
	else:
		return "Execution terminated by signal %s" % signal_number

class Location():
	def __init__(self, filename, line_number, column='', function='', params='', variable=''):
		self.filename = filename
		self.line_number = int(line_number)
		self.column = column
		self.function = function
		self.params = params
		self.variable = variable
	def __str__(self):
		return "Location(%s,%s,column=%s,function=%s,params=%s,variable=%s)" % (self.filename, self.line_number, self.column, self.function, self.params, self.variable)
	def description(self):
		params = self.params
		if self.function == 'main' and params.startswith('argc=1,'):
			params = ''
		return "in %s(%s) at\n%s:%s: %s" % (self.function, params, self.filename, self.line_number, self.source_line())
	def long_description(self, color):
		params = self.params
		if self.function == 'main' and params.startswith('argc=1,'):
			params = ''
#		d = f"in {self.function}({params}) in {color(self.filename, 'red')} at {color('line ' + str(self.line_number), 'red')}:\n\n" + self.surrounding_source(color, markMiddle=True)
		d = 'in ' + self.function + '(' + params + ') in ' + color(self.filename, 'red') + ' at ' + color('line ' + str(self.line_number), 'red') + ':\n\n' + self.surrounding_source(color, markMiddle=True)
		return d
	def source_line(self, clean=False):
		return fileline(self.filename, self.line_number, clean)
	def surrounding_source(self, color, radius=2, clean=False, markMiddle=False):
		source = ''
		for offset in range(-radius, radius+1):
			line = fileline(self.filename, self.line_number+offset, clean=clean)
			if markMiddle and offset == 0:
				line = color(re.sub(r'^ {0,3}', '-->', line), 'red')
			source += line
		return source
	def is_user_location(self):
		if not re.match(r'^[a-zA-Z]', self.function): return False 
		if re.match(r'^/(usr|build)/', self.filename): return False 
		if re.match(r'^\?', self.filename): return False 
		return True
	  
	
def fileline(filename, line_number, clean=False):
	line_number = int(line_number)
	if filename in source:
		if line_number < 0 or line_number > len(source[filename]):
			return ''
		if clean:
			return clean_c_source(source[filename][line_number - 1])
		return source[filename][line_number - 1]
	try:
		with open(filename) as f:
			source[filename] = f.readlines()
			for line in source[filename]:
				m = re.match(r'^\s*#\s*define\s*(\w+)\s*(.*\S)', line)
				if m:
					hash_define[filename][m.group(1)] = (line.rstrip(), m.group(2))
		line = source[filename][line_number - 1].rstrip() + "\n"
		if clean:
			line = clean_c_source(line)
		return line
	except IOError:
		debug_print(1, "fileline error can not open: %s" % (filename))
	except IndexError:
		pass
	return ""

# remove comments and truncate strings & character constants to zero-length
def clean_c_source(c_source, leave_white_space=False):
	c_source = re.sub("\\[\"']", '', c_source)
	c_source = re.sub(r'".*?"', '', c_source)
	c_source = re.sub(r"'.*?'", '', c_source)
	c_source = re.sub(r'/[/\*].*', '', c_source)
	if leave_white_space:
		return c_source
	return c_source.strip() + "\n"

def gdb_evaluate(expression):
	debug_print(1, 'gdb_evaluate:', expression,)
	value = gdb_execute('print %s' % expression)
	value = re.sub('^[^=]*=\s*', '', value).strip()
	debug_print(1, '->', value,)
	return value.strip()

def gdb_execute(command):
	debug_print(2, 'gdb.execute:', command)
	str = gdb.execute(command, to_string=True)
	debug_print(2, 'gdb.execute:', '->', str)
	return str
	
def parse_gdb_stack_frame(line):
	# note don't match function names starting with _ these are not user functions
	m = re.match(
		r'^\s*#(?P<frame_number>\d+)\s+(0x[0-9a-f]+\s+in+\s+)?'
		r'(?P<function>[a-zA-Z][^\s\(]*).*\((?P<params>.*)\)\s+at\s+'
		r'(?P<filename>[^\s:]+):(?P<line_number>\d+)\s*$',
			line)
	debug_print(1, 'parse_gdb_stack_frame', m != None, line)
	if m:
		filename = m.group('filename')
		if filename.startswith("/usr/") or filename.startswith("../sysdeps/"): 
			m = None
	return m
	
def gdb_set_frame():
	try:
		stack = gdb_execute('where')
		debug_print(1, "\nStack:\n",stack, "\n")
		stack_lines = stack.splitlines()
		frame = None
		for line in stack_lines:
			frame = parse_gdb_stack_frame(line)
			if frame and os.path.exists(frame.group('filename')):
				break
		if not frame:
			for line in reversed(stack_lines):
				frame = parse_gdb_stack_frame(line) or frame
		if not frame:
			debug_print(1, 'gdb_set_frame no frame number')
			return None
		gdb_execute('frame %s' % frame.group('frame_number'))
		return Location(frame.group('filename'), frame.group('line_number'), function=frame.group('function'), params=frame.group('params'))
	except:
		if debug: traceback.print_exc(file=sys.stderr)
	
def relevant_variables(c_source, color, arrays=[]):
	expressions = extract_expressions(c_source)
#	 arrays=[r'[a-z][a-zA-Z0-9_]*']
#	 debug_print(1, 'relevant_variables', arrays, c_source)
#	 for array in arrays:
#		 indices = extract_indices(array, c_source)
#		 expressions += indices
	done = set(['NULL', 'char','int', 'double', 'while', 'if', 'else', 'for', 'while', 'return']) # avoid trying to evaluate types/keywords for efficiency
	explanation = ''
	debug_print(1, 'relevant_variables expressions=', c_source, expressions)
	for expression in sorted(expressions, key=lambda e: (len(re.findall(r'\w+', e)), e)):
		try:
			expression = expression.strip()
			if expression not in done:
				done.add(expression)
				expression_value = evaluate_expression(expression, color)
				if expression_value is not None:
					explanation +=	"%s = %s\n" % (expression, expression_value)
		except RuntimeError as e:
			debug_print(1, 'print_variables_expressions: RuntimeError', e)
			pass
	if explanation:
		prefix = color('Values when execution stopped:', 'blue')
		explanation = prefix + '\n\n' + explanation
	return explanation

def evaluate_expression(expression, color):
	debug_print(1, 'expression:', expression)
	if re.match(r'^-?\s*[\d\.]+$', expression):
		return None	  # don't print(numbers)
	if re.search(r'[a-zA-Z0-9_]\s*\(', expression):
		return None	 # don't evaluate function calls
		
	expression_type = gdb_execute('whatis %s' % expression)
	expression_type = re.sub(r'\s*type\s*=\s*', '',	 expression_type).strip()
	debug_print(1, 'expresion_type=', expression_type)
	if re.search(r'\<|\)$', expression_type):
		return None

	expression_value = gdb_evaluate(expression)

	if (
		'_IO_FILE' in expression_value or
		'here_cg_arc_record' in	expression_value or
		expression_value == '<optimized out>' or
		len(expression_value) > 128
		):
		return None
		
	expression_value = re.sub(r'^0x[0-9a-f]+\s*(<.str>)?\s*"', '"', expression_value)
	if re.search(r'^\(.*\s+0x[0-9a-f]{4,}\s*$', expression_value):
		return None 
	expression_value = re.sub(r'^\s*\(\S+\s+\*\)\s*0x0\s*$', 'NULL', expression_value)
	if expression_type == 'char':
		m = re.match(r"^(-?\d+) '(.*)'$", expression_value)
		if m:
			ascii = int(m.group(1))
			if (0 < ascii < 9) or (13 < ascii < 32)	 or (ascii == 127):
				 expression_value = '%d (non-printable ASCII character)' % ascii
			elif ascii < 0 or ascii > 128:
				 expression_value = '%d (not valid ASCII)' % ascii
			elif ascii == 0:
				 expression_value = "0 = '\\0'"
			else:
				 expression_value = "%s = '%s'" % m.groups()
	expression_value = re.sub(r"'\000'", r"'\\0'", expression_value)

	warning_text = color(" <-- warning appears to be uninitialized value", 'red')

	for value in ['-1094795586', '-1.8325506472120096e-06', '-0.372548997', '-66 (not valid ASCII)']:
		expression_value = expression_value.replace(value, value + warning_text)

	return expression_value

def balance_bracket(str, depth=0):
#	 debug_print(1, 'balance_bracket(%s, %s)' % (str, depth))
	if not str:
		return ""
	elif str[0] == ']' or str[0] == ')':
		depth -= 1
	elif str[0] == '[' or str[0] == '(':
		depth += 1
	if depth < 0 and (len(str) == 1 or str[1] != '['):
		return ""
	return str[0] +	 balance_bracket(str[1:], depth)

	
def extract_expressions(c_source):
	expressions = []
	# match declaration
	m = re.match(r'[a-z][a-zA-Z0-9_]*\s+[a-z][a-zA-Z0-9_]*\s*\[(.*)', c_source, re.DOTALL)
	if m:
		 return extract_expressions(m.group(1))
	m = re.match(r'([a-z][a-zA-Z0-9_]*)\s*\[(.*)', c_source, re.DOTALL)
	if m:
		index = balance_bracket(m.group(2))
		if index:
			expressions = [m.group(1), index, m.group(1) + '[' + index + ']']
		remainder = m.group(2)
	else:
		m = re.match(r'([a-zA-Z][a-zA-Z0-9_]*)(.*)', c_source, re.DOTALL)
		if m:
			expressions = [m.group(1)]
			remainder = m.group(2)
		else:
			m = re.match(r'^[^a-zA-Z]+(.*)', c_source, re.DOTALL)
			if m:
				remainder = m.group(1)
			else:
				return []
	return expressions + extract_expressions(remainder)

def explain_location(loc, color):
	if not isinstance(loc, Location):
		return "Execution stopped at '%s'" % (loc)
	else:
		return 'Execution stopped here ' + loc.long_description(color)
#
# ensure the program compiled with dcc terminates after error
#
def kill_program1():
	if 'DCC_PID' in os.environ:
		try:
			os.kill(int(os.environ['DCC_PID']), signal.SIGPIPE)
			os.kill(int(os.environ['DCC_PID']), signal.SIGKILL)
		except ProcessLookupError:
			pass
	sys.exit(1)

def debug_print(level, *args, **kwargs):
	if debug >= level:
		kwargs['file'] = sys.stderr
		print(*args, **kwargs)

def interrupt_handler(signum, frame):
	if debug: print >>sys.stderr, 'signal caught'
	kill_program1()
	
if __name__ == '__main__':
	drive_gdb()
