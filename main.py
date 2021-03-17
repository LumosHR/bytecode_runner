import operator
import dis
import sys
import types
import inspect
import builtins


class Function(object):
    __slots__ = [
        'func_code', 'func_name', 'func_defaults',
        'func_globals', 'func_locals', 'func_dict',
        'func_closure', '__name__',
        '_vm', '_func',
    ]

    def __init__(self, name, code_obj, defaults, closure, vm):
        self._vm = vm
        self.func_code = code_obj
        self.func_name = self.__name__ = name or code_obj.co_name
        self.func_defaults = tuple(defaults)
        self.func_globals = self._vm.frame.f_globals
        self.func_locals = self._vm.frame.f_locals

        # 处理函数闭包
        self.func_closure = {}
        if closure:
            for i in range(len(code_obj.co_freevars)):
                self.func_closure[code_obj.co_freevars[i]] = closure[i]

        kw = {'argdefs': self.func_defaults}
        if closure:
            kw['closure'] = tuple(true_cell(0) for _ in closure)

        self._func = types.FunctionType(code_obj, self.func_globals, **kw)

    def __call__(self, *args, **kwargs):
        call_args = inspect.getcallargs(self._func, *args, **kwargs)
        frame = self._vm.make_frame(
            self.func_code, self.func_closure, call_args, self.func_globals, {}
        )
        return self._vm.run_frame(frame)


class Frame(object):
    def __init__(self, code_obj, global_names, local_names, prev_frame):
        self.code_obj = code_obj
        self.f_globals = global_names
        self.f_locals = local_names
        self.prev_frame = prev_frame
        # 数据栈
        self.stack = []
        if prev_frame:
            self.builtin_names = prev_frame.builtin_names
        else:
            self.builtin_names = local_names['__builtins__']
            if hasattr(self.builtin_names, '__dict__'):
                self.builtin_names = self.builtin_names.__dict__

        self.cells = {}
        # 在make_frame中已经将closure更新到local_names里面
        for i in code_obj.co_cellvars:
            self.cells[i] = local_names[i]

        for i in code_obj.co_freevars:
            self.cells[i] = local_names[i]

        self.last_instruction = 0


def true_cell(value):
    fn = (lambda x: lambda: x)(value)
    return fn.__closure__[0]


class VirtualMachineError(Exception):
    def __init__(self, msg):
        self.msg = msg

    def __str__(self):
        return str(self.msg)


class VirtualMachine(object):
    def __init__(self):
        # 程序调用栈
        self.frames = []
        self.frame = None
        self.return_value = None
        self.last_exception = None
        sys.stdout.write(str("===PyByterun===\n"))

    # 数据栈上的操作
    def inst_LOAD_CONST(self, val):
        self.push(val)

    def inst_POP_TOP(self):
        self.pop()

    def inst_DUP_TOP(self):
        self.push(self.top())

    def inst_DUP_TOP_TWO(self):
        x, y = self.popn(2)
        self.push(x, y, x, y)

    # 符号名称相关操作
    def inst_LOAD_NAME(self, name):
        frame = self.frame
        if name in frame.f_locals:
            val = frame.f_locals[name]
        elif name in frame.f_globals:
            val = frame.f_globals[name]
        elif name in frame.builtin_names:
            val = frame.builtin_names[name]
        else:
            raise NameError("name '%s' is not defined" % name)
        self.push(val)

    def inst_STORE_NAME(self, name):
        self.frame.f_locals[name] = self.pop()

    def inst_DELETE_NAME(self, name):
        self.frame.f_locals.pop(name)

    def inst_LOAD_FAST(self, name):
        if name.startswith('.'):
            name = 'implicit' + name[1:]
        if name in self.frame.f_locals:
            val = self.frame.f_locals[name]
        else:
            raise UnboundLocalError("local variable '%s' referenced before assignment" % name)
        self.push(val)

    def inst_STORE_FAST(self, name):
        self.frame.f_locals[name] = self.pop()

    def inst_GET_ITER(self):
        self.push(iter(self.pop()))

    def inst_FOR_ITER(self, jump):
        TOS = iter(self.top())
        try:
            self.push(next(TOS))
        except StopIteration:
            self.pop()
            self.jump(jump + 1)

    def inst_LOAD_GLOBAL(self, name):
        f = self.frame
        if name in f.f_globals:
            val = f.f_globals[name]
        elif name in f.builtin_names:
            val = f.builtin_names[name]
        else:
            raise NameError("global name '%s' is not defined" % name)
        self.push(val)

    def inst_UNPACK_SEQUENCE(self, count):
        args = self.pop()
        for x in reversed(args):
            self.push(x)

    def inst_STORE_GLOBAL(self, name):
        self.frame.f_globals[name] = self.pop()

    def inst_DELETE_GLOBAL(self, name):
        self.frame.f_globals.pop(name)

    def inst_LOAD_METHOD(self, name):
        obj = self.pop()
        self.push(getattr(obj, name))

    def inst_CALL_METHOD(self, arg):
        pos_args = self.popn(arg)
        func = self.pop()
        self.push(func(*pos_args))

    def inst_STORE_SUBSCR(self):
        val, obj, key = self.popn(3)
        obj[key] = val

    def inst_LOAD_ATTR(self, attr):
        obj = self.pop()
        val = getattr(obj, attr)
        self.push(val)

    def inst_STORE_ATTR(self, name):
        val, obj = self.popn(2)
        setattr(obj, name, val)

    def inst_DELETE_ATTR(self, name):
        obj = self.pop()
        delattr(obj, name)

    def inst_BUILD_LIST(self, count):
        args = self.popn(count)
        self.push(args)

    def inst_BUILD_TUPLE(self, count):
        args = self.popn(count)
        self.push(tuple(args))

    def inst_BUILD_SET(self, count):
        args = self.popn(count)
        self.push(set(args))

    def inst_BUILD_MAP(self, size):
        self.push({})

    def inst_LOAD_BUILD_CLASS(self):
        self.push(builtins.__build_class__)

    def inst_STORE_MAP(self):
        tarmap, val, key = self.popn(3)
        tarmap[key] = val
        self.push(tarmap)

    def inst_LIST_APPEND(self, count):
        val = self.pop()
        tar_list = self.frame.stack[-count]
        tar_list.append(val)

    def inst_IMPORT_NAME(self, name):
        level, fromlist = self.popn(2)
        frame = self.frame
        self.push(__import__(name, globals=frame.f_globals, locals=frame.f_locals, fromlist=fromlist, level=level))

    def inst_IMPORT_FROM(self, name):
        tmp = self.top()
        self.push(getattr(tmp, name))

    # 操作符
    BINARY_OPERATORS = {
        'POWER': pow,
        'MULTIPLY': operator.mul,
        'FLOOR_DIVIDE': operator.floordiv,
        'TRUE_DIVIDE': operator.truediv,
        'MODULO': operator.mod,
        'ADD': operator.add,
        'SUBTRACT': operator.sub,
        'SUBSCR': operator.getitem,
        'LSHIFT': operator.lshift,
        'RSHIFT': operator.rshift,
        'AND': operator.and_,
        'XOR': operator.xor,
        'OR': operator.or_,
    }

    def binaryOperator(self, op):
        x, y = self.popn(2)
        self.push(self.BINARY_OPERATORS[op](x, y))

    COMPARE_OPERATORS = [
        operator.lt,
        operator.le,
        operator.eq,
        operator.ne,
        operator.gt,
        operator.ge,
        lambda x, y: x in y,
        lambda x, y: x not in y,
        lambda x, y: x is y,
        lambda x, y: x is not y,
        lambda x, y: issubclass(x, Exception) and issubclass(x, y),
    ]

    def inst_COMPARE_OP(self, op):
        x, y = self.popn(2)
        self.push(self.COMPARE_OPERATORS[op](x, y))

    def inplaceOperator(self, op):
        x, y = self.popn(2)
        if op == 'POWER':
            x **= y
        elif op == 'MULTIPLY':
            x *= y
        elif op == 'MATRIX_MULTIPLY':
            x @= y
        elif op == 'FLOOR_DIVIDE':
            x //= y
        elif op == 'TRUE_DIVIDE':
            x /= y
        elif op == 'MODULO':
            x %= y
        elif op == 'ADD':
            x += y
        elif op == 'SUBTRACT':
            x -= y
        elif op == 'LSHIFT':
            x <<= y
        elif op == 'RSHIFT':
            x >>= y
        elif op == 'AND':
            x &= y
        elif op == 'XOR':
            x ^= y
        elif op == 'OR':
            x |= y
        else:
            raise VirtualMachineError("Unknown in-place operator: %r" % op)
        self.push(x)

    # 跳转指令
    def inst_JUMP_FORWARD(self, jump):
        self.jump(jump)

    def inst_JUMP_ABSOLUTE(self, jump):
        self.jump(jump)

    def inst_POP_JUMP_IF_TRUE(self, jump):
        val = self.pop()
        if val:
            self.jump(jump)

    def inst_POP_JUMP_IF_FALSE(self, jump):
        val = self.pop()
        if not val:
            self.jump(jump)

    def jump(self, jump):
        self.frame.last_instruction = jump

    # 函数操作
    def inst_MAKE_FUNCTION(self, flags):
        name = self.frame.stack.pop()
        code = self.frame.stack.pop()
        closure = None
        if flags == 0x08:
            closure = self.pop()
            defaults = []
        elif flags:
            defaults = self.pop()
        else:
            defaults = []
        fn = Function(name, code, defaults, closure, self)
        self.push(fn)

    def inst_CALL_FUNCTION(self, arg):
        name = self.pop()
        arg_func = self.pop()
        func = self.pop()
        retval = func(arg_func, name)
        self.push(retval)

    def inst_CALL_FUNCTION_KW(self, argc):
        kwargs_name = self.pop()
        kwargs = {}
        for i in kwargs_name[::-1]:
            kwargs[i] = self.pop()
        posargs = self.popn(argc - kwargs.__len__())
        func = self.pop()
        self.push(func(*posargs, **kwargs))

    def inst_LOAD_CLOSURE(self, i):
        code_obj = self.frame.code_obj
        if i < len(code_obj.co_cellvars):
            name = code_obj.co_cellvars[i]
        else:
            name = code_obj.co_freevars[i - len(code_obj.co_cellvars)]
        val = self.frame.f_locals[name]
        self.push(val)

    def inst_LOAD_DEREF(self, i):
        name = self.frame.code_obj.co_freevars[i]
        self.push(self.frame.cells[name])

    def inst_RETURN_VALUE(self):
        self.return_value = self.pop()
        return "return"

    # 输出操作
    def inst_PRINT_ITEM(self):
        item = self.pop()
        sys.stdout.write(str(item))

    def inst_PRINT_NEWLINE(self):
        print()

    def run_code(self, code, global_names=None, local_names=None):
        frame = self.make_frame(code, global_names=global_names, local_names=local_names)
        self.run_frame(frame)

    def make_frame(self, code, closure={}, call_args={}, global_names=None, local_names=None):
        if global_names is not None:
            global_names = global_names
            if local_names is None:
                local_names = global_names
        # 没给定全局名字空间，使用上一个帧的
        elif self.frames:
            global_names = self.frame.global_names
            local_names = {}
        # 否则定义全局和局部名字空间的初始状态
        else:
            global_names = local_names = {
                '__builtins__': builtins,
                '__name__': '__main__',
                '__doc__': None,
                '__package__': None,
            }
        # 将传入的call_args和closure更新到局部变量空间中
        local_names.update(call_args)
        local_names.update(closure)
        return Frame(code, global_names=global_names, local_names=local_names, prev_frame=self.frame)

    def push_frame(self, frame):
        self.frames.append(frame)
        self.frame = frame

    def pop_frame(self):
        self.frames.pop()
        if self.frames:
            self.frame = self.frames[-1]
        else:
            self.frame = None

    def run_frame(self, frame):
        # 运行frame直到返回
        self.push_frame(frame)

        while True:
            inst_name, arguments = self.parse_inst_and_args()
            res = self.dispatch(inst_name, arguments)

            if res:
                break

        self.pop_frame()

        if res == 'exception':
            exc, val, tb = self.last_exception
            e = exc(val)
            e.__traceback__ = tb
            raise e

        return self.return_value

    # 数据栈操作
    def top(self):
        return self.frame.stack[-1]

    def pop(self):
        return self.frame.stack.pop()

    def push(self, *vals):
        self.frame.stack.extend(vals)

    def popn(self, n):
        if n:
            ret = self.frame.stack[-n:]
            self.frame.stack[-n:] = []
            return ret
        else:
            return []

    # 解析指令
    def parse_inst_and_args(self):
        f = self.frame
        # 取待运行指令
        opoffset = f.last_instruction
        byteCode = f.code_obj.co_code[opoffset]
        inst_name = dis.opname[byteCode]

        f.last_instruction += 1
        # 若为有参指令
        if byteCode >= dis.HAVE_ARGUMENT:
            arg_val = f.code_obj.co_code[f.last_instruction]
            if byteCode in dis.hasconst:
                arg = f.code_obj.co_consts[arg_val]
            elif byteCode in dis.hasname:
                arg = f.code_obj.co_names[arg_val]
            elif byteCode in dis.haslocal:
                arg = f.code_obj.co_varnames[arg_val]
            elif byteCode in dis.hasjrel:
                arg = f.last_instruction + arg_val
            else:
                arg = arg_val
            argument = [arg]
        else:
            argument = []

        f.last_instruction += 1
        return inst_name, argument

    def dispatch(self, inst_name, argument):
        res = None
        try:
            bytecode_fn = getattr(self, 'inst_%s' % inst_name, None)
            if bytecode_fn is None:
                if inst_name.startswith('INPLACE_'):
                    self.inplaceOperator(inst_name[8:])
                elif inst_name.startswith('UNARY_'):
                    self.unaryOperator(inst_name[6:])
                elif inst_name.startswith('BINARY_'):
                    self.binaryOperator(inst_name[7:])
                elif inst_name == '<0>':
                    res = 'end_of_file'
                else:
                    raise VirtualMachineError("unsupported bytecode type: %s" % inst_name)
            else:
                res = bytecode_fn(*argument)
        except:
            self.last_exception = sys.exc_info()[:2] + (None,)
            res = 'exception'

        return res


import tkinter as tk
from tkinter import filedialog

root = tk.Tk()
root.withdraw()

filePath = filedialog.askopenfilename(filetypes=[('PY', '*.py'), ('All Files', '*')])
with open(filePath, 'r', encoding='utf-8') as file:
    target = file.read()
    code_obj = compile(target, "User code", "exec")
    with open('./code_object.txt', 'w') as store_file:
        dis.dis(x=code_obj, file=store_file)
    vm = VirtualMachine()
    vm.run_code(code_obj)
