from __future__ import annotations

from app.domain.models import Angles, Option, Paper, Question


def question(
    question_id: str,
    question_type: str,
    stem: str,
    options: tuple[tuple[str, str], ...],
    answer: str,
    explanation: str,
    angles: tuple[str, str, str, str],
    knowledge: tuple[str, ...],
) -> Question:
    return Question(
        id=question_id,
        type=question_type,
        stem=stem,
        options=tuple(Option(key, text) for key, text in options),
        answer=answer,
        explanation=explanation,
        angles=Angles(concept=angles[0], method=angles[1], mistake=angles[2], variant=angles[3]),
        knowledge=knowledge,
    )


def seed_papers() -> tuple[Paper, ...]:
    function_paper = Paper(
        id="functions-basics",
        title="函数与导数基础",
        subtitle="围绕单调性、切线和极值的自编检查卷",
        source="AI Learning OS seed",
        university=None,
        year=2026,
        subject="math",
        difficulty="core",
        duration_minutes=6,
        tags=("函数", "导数", "数学"),
        origin_url=None,
        license="CC0-1.0",
        questions=(
            question(
                "functions-1",
                "mcq",
                "函数 f(x)=x^3-3x 在区间 (0, +∞) 上的单调递增区间是哪一个？",
                (("A", "(0,1)"), ("B", "(1,+∞)"), ("C", "(0,+∞)"), ("D", "不存在")),
                "B",
                "f'(x)=3x^2-3，在 x>1 时为正，所以单调递增区间是 (1,+∞)。",
                (
                    "导数符号决定函数单调性。",
                    "求 f'(x)=3x^2-3，再解 f'(x)>0 且 x>0。",
                    "容易把 f'(x)>0 的完整解集 (-∞,-1) 和 (1,+∞) 忘掉区间限制。",
                    "若题目改为 (-∞,0)，递增区间应选 (-∞,-1)。",
                ),
                ("导数", "单调性"),
            ),
            question(
                "functions-2",
                "mcq",
                "曲线 y=x^2 在点 (2,4) 处的切线斜率是多少？",
                (("A", "2"), ("B", "4"), ("C", "8"), ("D", "0")),
                "B",
                "y'=2x，代入 x=2 得切线斜率为 4。",
                (
                    "切线斜率等于函数在该点的导数值。",
                    "先求导 y'=2x，再代入横坐标 2。",
                    "常见错误是把点的纵坐标 4 代入导函数。",
                    "求 y=x^3 在 x=1 处的切线斜率，应得到 3。",
                ),
                ("导数", "切线"),
            ),
            question(
                "functions-3",
                "tf",
                "可导函数在某点取得极值时，该点导数一定为 0。",
                (("T", "正确"), ("F", "错误")),
                "T",
                "可导函数的内部极值点满足导数为 0，这是费马引理的必要条件。",
                (
                    "极值点的必要条件与充分条件不同。",
                    "可导且取内部极值，则左右局部导数符号变化，导数必为 0。",
                    "容易把“导数为 0”误当成极值的充分条件。",
                    "导数为 0 但不是极值的例子是 y=x^3 在 x=0 处。",
                ),
                ("极值", "费马引理"),
            ),
        ),
    )

    algorithm_paper = Paper(
        id="algorithms-basics",
        title="算法复杂度入门",
        subtitle="自编的三题复杂度检查卷",
        source="AI Learning OS seed",
        university=None,
        year=2026,
        subject="computer science",
        difficulty="intro",
        duration_minutes=5,
        tags=("算法", "复杂度", "计算机科学"),
        origin_url=None,
        license="CC0-1.0",
        questions=(
            question(
                "algorithms-1",
                "mcq",
                "二分查找在一个已排序数组的平均时间复杂度是？",
                (("A", "O(1)"), ("B", "O(log n)"), ("C", "O(n)"), ("D", "O(n log n)")),
                "B",
                "每次比较把候选规模至少减半，所以是 O(log n)。",
                (
                    "时间复杂度描述输入规模增长时基本操作的增长速度。",
                    "每轮搜索空间减半，log2(n) 轮后剩余常数规模。",
                    "容易把最好情况的一次命中误认为平均情况。",
                    "若每轮分成三份并排除两份，仍是 O(log n)。",
                ),
                ("二分查找", "时间复杂度"),
            ),
            question(
                "algorithms-2",
                "tf",
                "归并排序的最好、平均和最坏时间复杂度都是 O(n log n)。",
                (("T", "正确"), ("F", "错误")),
                "T",
                "归并排序按中点稳定划分，递归深度 log n，每层合并共线性时间。",
                (
                    "分治算法的复杂度来自递归深度与每层合并成本。",
                    "画出递归树：log n 层，每层 O(n)。",
                    "容易把数组已排序时的归并过程误认为 O(n)。",
                    "快速排序最坏是 O(n^2)，与归并排序不同。",
                ),
                ("归并排序", "递归树"),
            ),
            question(
                "algorithms-3",
                "short",
                "用大 O 表示：顺序扫描一个长度为 n 的数组找最大值的时间复杂度是多少？",
                (),
                "O(n)",
                "每个元素最多检查一次，所以是 O(n)。",
                (
                    "顺序扫描每个元素一次。",
                    "循环执行 n 次，常数操作记为 O(n)。",
                    "容易把常数次比较写成 O(n^2)。",
                    "若只访问数组第一个元素，则是 O(1)。",
                ),
                ("复杂度", "线性扫描"),
            ),
        ),
    )

    return (function_paper, algorithm_paper)
